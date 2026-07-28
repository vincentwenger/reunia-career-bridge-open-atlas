from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zipfile import ZipFile

from xml.etree import ElementTree

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.parts.hdrftr import FooterPart, HeaderPart

from .docx_export import export_resume_docx
from .docx_styles import normalize_career_stage, normalize_resume_format
from .validation import adjacent_repeated_words
from .models import (
    ApprovedResume,
    BulletProposal,
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    SkillSet,
    TailoringProposal,
)

logger = logging.getLogger(__name__)

ReportStatus = Literal["pass", "warning", "fail", "info"]

_STATUS_SCORES: dict[ReportStatus, float] = {
    "pass": 100.0,
    "warning": 50.0,
    "fail": 0.0,
    "info": 0.0,
}

# Overall Resume Report weighting. Content that affects recruiter matching receives
# more weight than formatting-only checks.
_SECTION_WEIGHTS = {
    "Searchability": 0.15,
    "Hard skills": 0.25,
    "Soft skills": 0.08,
    "Content Quality": 0.15,
    "Recruiter tips": 0.12,
    "Formatting": 0.10,
    "Evidence & Gaps": 0.15,
}

# Show users where each report category is primarily improved. Categories that
# receive an additional final safety check also identify that verification stage.
_SECTION_WORKFLOW_OWNERSHIP: dict[str, tuple[str, str, str, str]] = {
    "Hard skills": ("Review Tailored Resume", "review", "", ""),
    "Evidence & Gaps": ("Evidence Review and Export", "evidence_export", "", ""),
    "Content Quality": ("Improve Resume Quality", "quality", "", ""),
    "Searchability": ("Improve Resume Quality", "quality", "", ""),
    "Recruiter tips": ("Improve Resume Quality", "quality", "", ""),
    "Formatting": ("Finalize Resume", "finalize", "", ""),
    "Soft skills": ("Improve Resume Quality", "quality", "", ""),
}


def _weighted_check_score(checks: list["ReportCheck"]) -> float:
    scored = [check for check in checks if check.status != "info" and check.weight > 0]
    if not scored:
        return 100.0
    total_weight = sum(check.weight for check in scored)
    return sum(check.score() * check.weight for check in scored) / total_weight


@dataclass(frozen=True)
class ReportCheck:
    label: str
    status: ReportStatus
    detail: str
    weight: float = 1.0
    score_value: float | None = None

    def score(self) -> float:
        value = _STATUS_SCORES[self.status] if self.score_value is None else self.score_value
        return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class ReportSubsection:
    name: str
    checks: list[ReportCheck] = field(default_factory=list)

    def score(self) -> float:
        return round(_weighted_check_score(self.checks), 1)


@dataclass(frozen=True)
class ReportSection:
    name: str
    intro: str
    subsections: list[ReportSubsection] = field(default_factory=list)

    @property
    def primary_workflow_step(self) -> str:
        return _SECTION_WORKFLOW_OWNERSHIP.get(self.name, ("", "", "", ""))[0]

    @property
    def primary_workflow_stage(self) -> str:
        return _SECTION_WORKFLOW_OWNERSHIP.get(self.name, ("", "", "", ""))[1]

    @property
    def verification_workflow_step(self) -> str:
        return _SECTION_WORKFLOW_OWNERSHIP.get(self.name, ("", "", "", ""))[2]

    @property
    def verification_workflow_stage(self) -> str:
        return _SECTION_WORKFLOW_OWNERSHIP.get(self.name, ("", "", "", ""))[3]

    def scored_checks(self) -> list[ReportCheck]:
        return [
            check
            for subsection in self.subsections
            for check in subsection.checks
            if check.status != "info"
        ]

    def score(self) -> float:
        return round(_weighted_check_score(self.scored_checks()), 1)

    def subsection_score(self, *names: str, exclude: bool = False) -> float:
        requested = set(names)
        checks = [
            check
            for subsection in self.subsections
            if (subsection.name not in requested if exclude else subsection.name in requested)
            for check in subsection.checks
        ]
        return round(_weighted_check_score(checks), 1)


@dataclass(frozen=True)
class ResumeReport:
    searchability: ReportSection
    hard_skills: ReportSection
    soft_skills: ReportSection
    content_quality: ReportSection
    recruiter_tips: ReportSection
    formatting: ReportSection
    evidence_gaps: ReportSection

    def sections(self) -> list[ReportSection]:
        # Present report sections from highest to lowest decision value:
        # job-specific fit and evidence first, then quality/ATS checks,
        # followed by advisory and presentation-focused guidance.
        return [
            self.hard_skills,
            self.evidence_gaps,
            self.content_quality,
            self.searchability,
            self.recruiter_tips,
            self.formatting,
            self.soft_skills,
        ]

    def overall_score(self) -> float:
        weighted = sum(
            section.score() * _SECTION_WEIGHTS[section.name]
            for section in self.sections()
        )
        return round(weighted, 1)

    def job_match_score(self) -> float:
        # Match score focuses on job-specific content and verified evidence rather
        # than static document quality.
        fit_score = (
            self.searchability.subsection_score("Job Title Match")
            + self.searchability.subsection_score("Education Match")
            + self.recruiter_tips.subsection_score("Job Level Match")
        ) / 3
        semantic_score = self.content_quality.subsection_score("Semantic Match")
        weighted = (
            self.hard_skills.score() * 0.35
            + self.evidence_gaps.score() * 0.25
            + semantic_score * 0.20
            + fit_score * 0.15
            + self.soft_skills.score() * 0.05
        )
        return round(weighted, 1)

    def resume_quality_score(self) -> float:
        # Quality excludes job-fit checks and focuses on ATS structure,
        # readability, recruiter presentation, and document formatting.
        searchability_quality = self.searchability.subsection_score(
            "Job Title Match", "Education Match", exclude=True
        )
        recruiter_quality = self.recruiter_tips.subsection_score(
            "Job Level Match", exclude=True
        )
        weighted = (
            searchability_quality * 0.25
            + recruiter_quality * 0.20
            + self.content_quality.subsection_score("Semantic Match", exclude=True) * 0.30
            + self.formatting.score() * 0.25
        )
        return round(weighted, 1)


@dataclass(frozen=True)
class EvidenceGapRow:
    requirement_id: str
    priority: str
    category: str
    requirement: str
    evidence_status: str
    appears_in_resume: bool
    evidence_locations: list[str] = field(default_factory=list)
    rationale: str = ""
    recommended_action: str = ""
    score: float = 0.0
    report_status: ReportStatus = "info"


@dataclass(frozen=True)
class EvidenceGapSummary:
    supported: int
    partial: int
    unsupported: int
    candidate_confirmations: int


def build_initial_resume_proposal(
    profile: CandidateProfile,
    evidence_source: TailoringProposal | None = None,
) -> TailoringProposal:
    """Represent the candidate profile exactly as the untailored resume content."""
    return TailoringProposal(
        professional_summary=profile.current_summary,
        skills=SkillSet(
            hard_skills=list(profile.skills.hard_skills),
            soft_skills=list(profile.skills.soft_skills),
            tools_software=list(profile.skills.tools_software),
            industry_knowledge=list(profile.skills.industry_knowledge),
        ),
        bullet_proposals=[
            BulletProposal(
                source_bullet_id=bullet.id,
                include=True,
                proposed_text=bullet.text,
                matched_requirement_ids=[],
                evidence_note="Original wording from the candidate profile.",
            )
            for experience in profile.experiences
            for bullet in experience.bullets
        ],
        evidence_matches=(
            [item.model_copy(deep=True) for item in evidence_source.evidence_matches]
            if evidence_source
            else []
        ),
        unsupported_requirements=(
            list(evidence_source.unsupported_requirements) if evidence_source else []
        ),
        candidate_questions=(
            [item.model_copy(deep=True) for item in evidence_source.candidate_questions]
            if evidence_source
            else []
        ),
    )


def initial_resume_title(profile: CandidateProfile) -> str:
    """Use the most recent documented role as the untailored resume profile title."""
    if profile.experiences and profile.experiences[0].title.strip():
        return profile.experiences[0].title.strip()
    return "Professional"


_DATE_RANGE_PATTERN = re.compile(
    r"^(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}\s*[-–—]\s*"
    r"(?:(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}|present|current)$",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?%?\b|\b(?:dozens?|hundreds?|thousands?|millions?)\b", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z'/-]*")

_ACTION_VERBS = {
    "achieved",
    "analyzed",
    "automated",
    "built",
    "collaborated",
    "configured",
    "created",
    "delivered",
    "designed",
    "developed",
    "directed",
    "drove",
    "evaluated",
    "executed",
    "implemented",
    "improved",
    "increased",
    "identified",
    "integrated",
    "led",
    "managed",
    "migrated",
    "optimized",
    "performed",
    "presented",
    "reduced",
    "resolved",
    "streamlined",
    "tested",
    "trained",
    "transformed",
    "validated",
}


_CLICHES_AND_BUZZWORDS = {
    "best in class",
    "detail oriented",
    "dynamic professional",
    "excellent communication skills",
    "fast paced environment",
    "go getter",
    "hard worker",
    "hit the ground running",
    "innovative thinker",
    "outside the box",
    "passionate professional",
    "proactive self starter",
    "proven track record",
    "results driven",
    "rockstar",
    "seasoned professional",
    "strategic thinker",
    "synergy",
    "team player",
    "thought leader",
    "value added",
    "wear many hats",
    "world class",
}

_NEGATIVE_RESUME_PHRASES = {
    "failed to",
    "fired from",
    "lack of",
    "poor performance",
    "struggled to",
    "terminated from",
    "unable to",
    "weak performance",
}

_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

_TITLE_LEVEL_MINIMUM_YEARS = {
    "senior": 5,
    "lead": 7,
    "staff": 7,
    "principal": 8,
    "manager": 5,
    "director": 8,
    "vice president": 10,
    "vp": 10,
    "head": 10,
}

_ENTRY_LEVEL_TITLE_TERMS = {"entry level", "entry-level", "junior", "intern", "graduate"}

_STANDARD_FONTS = {
    "arial",
    "aptos",
    "calibri",
    "cambria",
    "garamond",
    "georgia",
    "helvetica",
    "tahoma",
    "times new roman",
    "trebuchet ms",
    "verdana",
}

_COMMON_PUNCTUATION = set(".,;:!?\"'()[]{}<>-–—_/\\&%+#|@=$*")

_HARD_SKILL_PRIORITY_ORDER = {"critical": 0, "important": 1, "secondary": 2}

_SOFT_SKILL_MARKERS = {
    "adaptability",
    "adaptable",
    "analytical",
    "attention to detail",
    "coach",
    "coaching",
    "collaboration",
    "collaborative",
    "communication",
    "conflict resolution",
    "creative",
    "creativity",
    "critical thinking",
    "decision making",
    "delegation",
    "empathy",
    "flexibility",
    "influence",
    "influencing",
    "interpersonal",
    "leadership",
    "listening",
    "mentoring",
    "negotiation",
    "organization",
    "organizational",
    "presentation",
    "problem solving",
    "relationship building",
    "resilience",
    "self starter",
    "stakeholder communication",
    "stakeholder management",
    "strategic thinking",
    "teamwork",
    "time management",
    "training",
    "verbal communication",
    "written communication",
}

_COMMON_MISSPELLINGS = {
    "acheived": "achieved",
    "accomodated": "accommodated",
    "adress": "address",
    "analized": "analyzed",
    "collaberated": "collaborated",
    "commited": "committed",
    "developped": "developed",
    "enviroment": "environment",
    "experiance": "experience",
    "implimented": "implemented",
    "managment": "management",
    "occured": "occurred",
    "performence": "performance",
    "recieved": "received",
    "responsability": "responsibility",
    "seperate": "separate",
    "succesful": "successful",
    "sucessfully": "successfully",
    "teh": "the",
    "thier": "their",
    "untill": "until",
}

_PERSONAL_PRONOUN_PATTERN = re.compile(
    r"\b(?:i|me|my|mine|myself|we|our|ours|ourselves)\b",
    re.IGNORECASE,
)
_NUMBER_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*%|\s*(?:k|m|b|million|billion))?",
    re.IGNORECASE,
)
_IRREGULAR_PAST_OPENERS = {
    "built",
    "brought",
    "cut",
    "drove",
    "grew",
    "led",
    "made",
    "oversaw",
    "ran",
    "saw",
    "sold",
    "taught",
    "won",
    "wrote",
}
_SECTION_HEADING_ALIASES = (
    (
        "summary",
        (
            "professional summary",
            "technical profile",
            "professional profile",
            "summary",
            "profile",
        ),
    ),
    (
        "skills",
        (
            "skills",
            "core competencies",
            "technical skills",
            "transferable and relevant skills",
            "core capabilities",
        ),
    ),
    (
        "education",
        (
            "education",
            "education and professional development",
            "education and certifications",
            "education and credentials",
        ),
    ),
    (
        "experience",
        (
            "work experience",
            "professional experience",
            "engineering experience",
            "relevant experience",
            "client and project experience",
            "experience",
        ),
    ),
)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _words(value: str) -> list[str]:
    return _WORD_PATTERN.findall(value)


def _month_index(value: str) -> int | None:
    match = re.fullmatch(r"(0?[1-9]|1[0-2])/(19|20)\d{2}", value.strip())
    if not match:
        return None
    month_text, year_text = value.strip().split("/")
    return int(year_text) * 12 + int(month_text) - 1


def _documented_experience_years(profile: CandidateProfile) -> float:
    intervals: list[tuple[int, int]] = []
    current_month = date.today().year * 12 + date.today().month - 1
    for experience in profile.experiences:
        parts = re.split(r"\s*[-–—]\s*", experience.dates.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        start = _month_index(parts[0])
        end = current_month if parts[1].casefold() in {"present", "current"} else _month_index(parts[1])
        if start is None or end is None or end < start:
            continue
        intervals.append((start, end))

    if not intervals:
        return 0.0

    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    months = sum(end - start + 1 for start, end in merged)
    return months / 12.0


def _replace_number_words(value: str) -> str:
    result = value.casefold()
    for word, number in _NUMBER_WORDS.items():
        result = re.sub(rf"\b{word}\b", number, result)
    return result


def _required_experience_years(analysis: JobAnalysis) -> int | None:
    minimums: list[int] = []
    for requirement in analysis.requirements:
        text = _replace_number_words(requirement.requirement)
        range_spans: list[tuple[int, int]] = []
        for match in re.finditer(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:years?|yrs?)\b", text):
            minimums.append(int(match.group(1)))
            range_spans.append(match.span())
        for match in re.finditer(r"\b(\d{1,2})\s*(?:\+|plus|or more)?\s*(?:years?|yrs?)\b", text):
            if any(start <= match.start() < end for start, end in range_spans):
                continue
            minimums.append(int(match.group(1)))
    return max(minimums) if minimums else None


def _job_level_match(profile: CandidateProfile, analysis: JobAnalysis) -> ReportCheck:
    years = _documented_experience_years(profile)
    required_years = _required_experience_years(analysis)
    years_display = f"{years:.1f}".rstrip("0").rstrip(".")

    if required_years is not None:
        status: ReportStatus = "pass" if years >= required_years else "fail"
        detail = (
            f"The resume documents approximately {years_display} years of work experience, "
            f"compared with a detected minimum requirement of {required_years} years. "
            "Carefully review all other job criteria to confirm a strong overall match before applying."
        )
        return ReportCheck("Your years of experience align with the role's requirements", status, detail)

    normalized_title = _normalize(analysis.target_title)
    for level, minimum in _TITLE_LEVEL_MINIMUM_YEARS.items():
        if re.search(rf"\b{re.escape(level)}\b", normalized_title):
            status = "pass" if years >= minimum else "fail"
            return ReportCheck(
                "Your years of experience align with the role's requirements",
                status,
                f'The title "{analysis.target_title}" suggests a {minimum}+ year experience level, and the resume documents approximately {years_display} years. Carefully review all other job criteria before applying.',
            )

    if any(term in analysis.target_title.casefold() for term in _ENTRY_LEVEL_TITLE_TERMS) and years > 5:
        return ReportCheck(
            "Your years of experience align with the role's requirements",
            "warning",
            f'The resume documents approximately {years_display} years of experience, while "{analysis.target_title}" appears entry-level. Consider whether the role, compensation, and growth path fit your experience.',
        )

    return ReportCheck(
        "Your years of experience align with the role's requirements",
        "pass",
        f"The resume documents approximately {years_display} years of work experience, and no explicit minimum-years mismatch was detected. Carefully review all other job criteria to confirm a strong overall match before applying.",
    )


def _professional_web_links(
    template_path: str | Path | None,
    profile: CandidateProfile | None = None,
) -> tuple[list[str], str | None]:
    targets: list[str] = []
    if profile is not None:
        targets.extend(
            url.strip()
            for url in (profile.contact.linkedin_url, profile.contact.github_url)
            if url.strip().casefold().startswith(("http://", "https://"))
        )

    if template_path:
        try:
            with ZipFile(str(template_path)) as archive:
                for name in archive.namelist():
                    if not name.startswith("word/") or not name.endswith(".rels"):
                        continue
                    root = ElementTree.fromstring(archive.read(name))
                    for relationship in root:
                        target = relationship.attrib.get("Target", "").strip()
                        relation_type = relationship.attrib.get("Type", "")
                        if relation_type.endswith("/hyperlink") and target.casefold().startswith(("http://", "https://")):
                            targets.append(target)
        except Exception as exc:  # pragma: no cover - defensive UI fallback
            if not targets:
                return [], f"The resume hyperlinks could not be inspected: {exc}"
    elif not targets:
        return [], "No resume template or candidate web links were supplied for hyperlink inspection."

    excluded_social = {"facebook.com", "instagram.com", "tiktok.com", "twitter.com", "x.com"}
    professional: list[str] = []
    for target in targets:
        hostname = (urlparse(target).hostname or "").casefold().removeprefix("www.")
        if not hostname or any(hostname == domain or hostname.endswith("." + domain) for domain in excluded_social):
            continue
        professional.append(hostname)
    return sorted(set(professional)), None


def _estimated_resume_word_count(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    resume_title: str,
) -> int:
    selected_lookup = {
        item.source_bullet_id: item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    parts = [
        profile.name,
        profile.contact.location,
        profile.contact.phone,
        profile.contact.email,
        profile.contact.linkedin_label,
        profile.contact.github_label,
        resume_title,
        "Professional Summary",
        proposal.professional_summary,
        "Skills",
        *proposal.skills.hard_skills,
        *proposal.skills.soft_skills,
        *proposal.skills.tools_software,
        *proposal.skills.industry_knowledge,
        *profile.skills.languages,
        "Education",
    ]
    for education in profile.education:
        parts.extend(
            [education.credential, education.institution, education.location, education.date, education.detail]
        )
    parts.append("Work Experience")
    for experience in profile.experiences:
        parts.extend([experience.employer, experience.location, experience.dates, experience.title])
        parts.extend(
            selected_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in selected_lookup
        )
    return sum(len(_words(part)) for part in parts if part)


def _selected_bullets(proposal: TailoringProposal) -> list[str]:
    return [
        item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    ]


def _selected_bullet_ids_by_experience(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> dict[str, list[str]]:
    selected_ids = {
        item.source_bullet_id
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    return {
        experience.id: [bullet.id for bullet in experience.bullets if bullet.id in selected_ids]
        for experience in profile.experiences
    }



def _proposed_resume_text(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    resume_title: str,
) -> str:
    """Return the searchable text of the proposed resume."""
    selected_lookup = {
        item.source_bullet_id: item.proposed_text.strip()
        for item in proposal.bullet_proposals
        if item.include and item.proposed_text.strip()
    }
    parts = [
        profile.name,
        profile.contact.location,
        profile.contact.phone,
        profile.contact.email,
        profile.contact.linkedin_label,
        profile.contact.github_label,
        resume_title,
        "Professional Summary",
        proposal.professional_summary,
        "Skills",
        *proposal.skills.hard_skills,
        *proposal.skills.soft_skills,
        *proposal.skills.tools_software,
        *proposal.skills.industry_knowledge,
        *profile.skills.languages,
        "Education",
    ]
    for education in profile.education:
        parts.extend(
            [education.credential, education.institution, education.location, education.date, education.detail]
        )
    parts.append("Work Experience")
    for experience in profile.experiences:
        parts.extend([experience.employer, experience.location, experience.dates, experience.title])
        parts.extend(
            selected_lookup[bullet.id]
            for bullet in experience.bullets
            if bullet.id in selected_lookup
        )
    return "\n".join(part for part in parts if part)


def _exact_phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    """Build a case-insensitive exact-phrase pattern with flexible whitespace."""
    cleaned = phrase.strip()
    if not cleaned:
        return None
    escaped = re.escape(cleaned).replace(r"\ ", r"\s+")
    prefix = r"(?<![A-Za-z0-9])" if cleaned[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if cleaned[-1].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _exact_phrase_count(text: str, phrase: str) -> int:
    pattern = _exact_phrase_pattern(phrase)
    return len(pattern.findall(text)) if pattern else 0


def _job_skill_entries(
    analysis: JobAnalysis,
    job_description: str,
) -> list[dict[str, object]]:
    """Collect deduplicated hard-skill keywords and their job-description frequency."""
    hard_requirements = [
        requirement
        for requirement in analysis.requirements
        if requirement.category in {"technical_skill", "domain_knowledge", "methodology"}
    ]
    source_text = job_description.strip() or "\n".join(
        requirement.requirement for requirement in hard_requirements
    )
    entries: dict[str, dict[str, object]] = {}

    for requirement in hard_requirements:
        candidates = requirement.keywords or [requirement.requirement]
        for raw_skill in candidates:
            skill = re.sub(r"\s+", " ", raw_skill).strip(" \t\r\n,;:.")
            if not skill or len(_words(skill)) > 8 or len(skill) > 80:
                continue
            key = skill.casefold()
            pattern = _exact_phrase_pattern(skill)
            match = pattern.search(source_text) if pattern else None
            display = re.sub(r"\s+", " ", match.group(0)).strip() if match else skill
            job_count = _exact_phrase_count(source_text, display)

            entry = entries.setdefault(
                key,
                {
                    "skill": display,
                    "job_count": job_count,
                    "priority": requirement.priority,
                    "requirement_ids": [],
                },
            )
            if job_count > int(entry["job_count"]):
                entry["skill"] = display
                entry["job_count"] = job_count
            if _HARD_SKILL_PRIORITY_ORDER[requirement.priority] < _HARD_SKILL_PRIORITY_ORDER[str(entry["priority"])]:
                entry["priority"] = requirement.priority
            requirement_ids = entry["requirement_ids"]
            if requirement.id not in requirement_ids:
                requirement_ids.append(requirement.id)

    return sorted(
        entries.values(),
        key=lambda item: (
            -int(item["job_count"]),
            _HARD_SKILL_PRIORITY_ORDER[str(item["priority"])],
            str(item["skill"]).casefold(),
        ),
    )



_SKILL_PRIORITY_WEIGHTS = {
    "critical": 1.50,
    "important": 1.20,
    "secondary": 1.00,
}


def _skill_check_score(
    *,
    resume_count: int,
    job_count: int,
    all_unsupported: bool,
    any_partial: bool,
) -> tuple[ReportStatus, float, float]:
    coverage = min(resume_count / max(job_count, 1), 1.0)
    if all_unsupported:
        return "fail", 0.0, coverage
    score = coverage * (50.0 if any_partial else 100.0)
    if score >= 99.95:
        status: ReportStatus = "pass"
    elif score <= 0.05:
        status = "fail"
    else:
        status = "warning"
    return status, score, coverage

def _hard_skill_comparison_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    job_description: str,
    resume_title: str,
) -> ReportSubsection:
    resume_text = _proposed_resume_text(profile, analysis, proposal, resume_title)
    evidence_lookup = {match.requirement_id: match for match in proposal.evidence_matches}
    checks: list[ReportCheck] = []

    for entry in _job_skill_entries(analysis, job_description):
        skill = str(entry["skill"])
        job_count = int(entry["job_count"])
        resume_count = _exact_phrase_count(resume_text, skill)
        priority = str(entry["priority"])
        evidence = [
            evidence_lookup[requirement_id]
            for requirement_id in entry["requirement_ids"]
            if requirement_id in evidence_lookup
        ]
        all_unsupported = bool(evidence) and all(match.status == "unsupported" for match in evidence)
        any_partial = any(match.status == "partial" for match in evidence)
        status, score_value, coverage = _skill_check_score(
            resume_count=resume_count,
            job_count=job_count,
            all_unsupported=all_unsupported,
            any_partial=any_partial,
        )
        weight = max(job_count, 1) * _SKILL_PRIORITY_WEIGHTS.get(priority, 1.0)

        count_text = (
            f"Resume: {resume_count}, Job description: {job_count}. "
            f"Weighted coverage: {coverage * 100:.1f}%."
        )
        if resume_count == 0:
            if all_unsupported:
                detail = (
                    f"{count_text} This {priority} hard skill is not supported by verified evidence. "
                    "Treat it as a gap and do not add it unless the candidate confirms relevant experience."
                )
            else:
                detail = (
                    f"{count_text} The exact job-description spelling is missing from the proposed resume. "
                    "Add or emphasize it only where verified evidence supports the claim."
                )
        elif all_unsupported:
            detail = (
                f"{count_text} The term appears in the resume even though the mapped requirement lacks verified evidence. "
                "Remove it or confirm supporting experience before export."
            )
        elif any_partial:
            detail = (
                f"{count_text} The exact spelling is present, but the supporting evidence is partial. "
                "Its score is reduced until the candidate verifies the claim."
            )
        elif coverage < 1.0:
            detail = (
                f"{count_text} The skill is present, but it appears less often than in the job description. "
                "Strengthen it naturally in evidence-based content when the additional emphasis is accurate."
            )
        else:
            frequency_note = (
                " It is one of the most repeated hard skills in the job description, and the proposed resume provides full frequency coverage."
                if job_count >= 2
                else " The exact spelling is represented with full frequency coverage."
            )
            detail = count_text + frequency_note
        checks.append(
            ReportCheck(
                skill,
                status,
                detail,
                weight=weight,
                score_value=score_value,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No explicit hard skills identified",
                "info",
                "The job analyzer did not return technical, domain, or methodology keywords for comparison.",
            )
        )
    return ReportSubsection("Skill comparison", checks)

def _looks_like_soft_skill(value: str) -> bool:
    normalized = _normalize(value)
    return any(
        marker == normalized
        or re.search(rf"\b{re.escape(marker)}\b", normalized)
        for marker in _SOFT_SKILL_MARKERS
    )


def _job_soft_skill_entries(
    analysis: JobAnalysis,
    job_description: str,
) -> list[dict[str, object]]:
    """Collect deduplicated soft-skill keywords and their exact job-description frequency."""
    soft_requirements = []
    for requirement in analysis.requirements:
        candidates = requirement.keywords or [requirement.requirement]
        if requirement.category == "leadership" or any(
            _looks_like_soft_skill(candidate) for candidate in candidates
        ):
            soft_requirements.append(requirement)

    source_text = job_description.strip() or "\n".join(
        requirement.requirement for requirement in soft_requirements
    )
    entries: dict[str, dict[str, object]] = {}

    for requirement in soft_requirements:
        candidates = requirement.keywords or [requirement.requirement]
        for raw_skill in candidates:
            skill = re.sub(r"\s+", " ", raw_skill).strip(" \t\r\n,;:.")
            if (
                not skill
                or len(_words(skill)) > 8
                or len(skill) > 80
                or (requirement.category != "leadership" and not _looks_like_soft_skill(skill))
            ):
                continue
            key = skill.casefold()
            pattern = _exact_phrase_pattern(skill)
            match = pattern.search(source_text) if pattern else None
            display = re.sub(r"\s+", " ", match.group(0)).strip() if match else skill
            job_count = _exact_phrase_count(source_text, display)
            if job_description.strip() and job_count == 0:
                continue

            entry = entries.setdefault(
                key,
                {
                    "skill": display,
                    "job_count": job_count,
                    "priority": requirement.priority,
                    "requirement_ids": [],
                },
            )
            if job_count > int(entry["job_count"]):
                entry["skill"] = display
                entry["job_count"] = job_count
            if _HARD_SKILL_PRIORITY_ORDER[requirement.priority] < _HARD_SKILL_PRIORITY_ORDER[str(entry["priority"])]:
                entry["priority"] = requirement.priority
            requirement_ids = entry["requirement_ids"]
            if requirement.id not in requirement_ids:
                requirement_ids.append(requirement.id)

    return sorted(
        entries.values(),
        key=lambda item: (
            -int(item["job_count"]),
            _HARD_SKILL_PRIORITY_ORDER[str(item["priority"])],
            str(item["skill"]).casefold(),
        ),
    )


def _soft_skill_comparison_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    job_description: str,
    resume_title: str,
) -> ReportSubsection:
    resume_text = _proposed_resume_text(profile, analysis, proposal, resume_title)
    evidence_lookup = {match.requirement_id: match for match in proposal.evidence_matches}
    checks: list[ReportCheck] = []

    for entry in _job_soft_skill_entries(analysis, job_description):
        skill = str(entry["skill"])
        job_count = int(entry["job_count"])
        resume_count = _exact_phrase_count(resume_text, skill)
        priority = str(entry["priority"])
        evidence = [
            evidence_lookup[requirement_id]
            for requirement_id in entry["requirement_ids"]
            if requirement_id in evidence_lookup
        ]
        all_unsupported = bool(evidence) and all(match.status == "unsupported" for match in evidence)
        any_partial = any(match.status == "partial" for match in evidence)
        status, score_value, coverage = _skill_check_score(
            resume_count=resume_count,
            job_count=job_count,
            all_unsupported=all_unsupported,
            any_partial=any_partial,
        )
        weight = max(job_count, 1) * _SKILL_PRIORITY_WEIGHTS.get(priority, 1.0)

        count_text = (
            f"Resume: {resume_count}, Job description: {job_count}. "
            f"Weighted coverage: {coverage * 100:.1f}%."
        )
        if resume_count == 0:
            if all_unsupported:
                detail = (
                    f"{count_text} This {priority} soft skill is not supported by verified evidence. "
                    "Treat it as a gap and do not add it unless the candidate can support it with a real example."
                )
            else:
                detail = (
                    f"{count_text} The exact job-description spelling is missing from the proposed resume. "
                    "Add it only when verified experience demonstrates the trait or ability."
                )
        elif all_unsupported:
            detail = (
                f"{count_text} The term appears in the resume even though the mapped requirement lacks verified evidence. "
                "Remove it or confirm a supporting example before export."
            )
        elif any_partial:
            detail = (
                f"{count_text} The exact spelling is present, but the supporting evidence is partial. "
                "Its score is reduced until the candidate can verify a concrete example."
            )
        elif coverage < 1.0:
            detail = (
                f"{count_text} The skill is present but appears less frequently than in the job description. "
                "Keep the wording natural and prioritize stronger hard-skill evidence before repeating soft skills."
            )
        else:
            frequency_note = (
                " It is repeated in the job description and the proposed resume provides full frequency coverage."
                if job_count >= 2
                else " The exact spelling is represented with full frequency coverage."
            )
            detail = count_text + frequency_note
        checks.append(
            ReportCheck(
                skill,
                status,
                detail,
                weight=weight,
                score_value=score_value,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No explicit soft skills identified",
                "info",
                "The job analyzer did not identify communication, leadership, collaboration, analytical, coaching, or similar traits for comparison.",
            )
        )
    return ReportSubsection("Skill comparison", checks)


def _requirement_is_represented(requirement, proposal: TailoringProposal) -> bool:
    included_requirement_ids = {
        requirement_id
        for bullet in proposal.bullet_proposals
        if bullet.include
        for requirement_id in bullet.matched_requirement_ids
    }
    if requirement.id in included_requirement_ids:
        return True

    resume_text = " ".join(
        [
            proposal.professional_summary,
            *proposal.skills.hard_skills,
            *proposal.skills.soft_skills,
            *proposal.skills.tools_software,
            *proposal.skills.industry_knowledge,
            *[
                bullet.proposed_text
                for bullet in proposal.bullet_proposals
                if bullet.include and bullet.proposed_text.strip()
            ],
        ]
    )
    normalized_resume = _normalize(resume_text)
    terms = [*requirement.keywords, requirement.requirement]
    for term in terms:
        normalized_term = _normalize(term)
        if normalized_term and normalized_term in normalized_resume:
            return True
    return False


def _evidence_location_lookup(profile: CandidateProfile) -> dict[str, str]:
    locations: dict[str, str] = {}
    for experience in profile.experiences:
        locations[experience.id] = f"{experience.employer} — {experience.title}"
        for bullet in experience.bullets:
            locations[bullet.id] = (
                f"{bullet.id} — {experience.employer}: {bullet.text}"
            )

    for skill in profile.skills.all_non_language_skills():
        locations.setdefault(skill, f"Verified skill — {skill}")
    for language in profile.skills.languages:
        locations.setdefault(language, f"Verified language — {language}")
    for evidence in profile.supplemental_evidence:
        locations[evidence.id] = f"Candidate confirmation — {evidence.statement}"
        for skill in evidence.verified_skills:
            locations.setdefault(skill, f"Candidate-confirmed skill — {skill}")
    return locations


_EVIDENCE_PRIORITY_WEIGHTS = {
    "critical": 3.0,
    "important": 2.0,
    "secondary": 1.0,
}


def _evidence_requirement_result(
    status: str,
    represented: bool,
    acknowledged_no: bool,
) -> tuple[ReportStatus, float, str]:
    if status == "supported" and represented:
        return "pass", 100.0, "Verified evidence supports the requirement and it is represented in the resume."
    if status == "supported":
        return "warning", 75.0, "Verified evidence supports the requirement, but the resume does not currently emphasize it."
    if status == "partial" and represented:
        return "warning", 60.0, "The requirement is represented conservatively, but the supporting evidence is only partial."
    if status == "partial":
        return "warning", 40.0, "Only partial evidence exists and the requirement is not currently represented in the resume."
    if represented:
        return "fail", 0.0, "The requirement appears in the resume without verified supporting evidence. Remove it or confirm the experience."
    if acknowledged_no:
        return "fail", 20.0, "The candidate explicitly confirmed that this requirement is not applicable. It remains an acknowledged gap."
    return "fail", 10.0, "The requirement is unsupported or unresolved and is not represented in the resume."


def _negative_answer_requirement_ids(
    candidate_answers: list[CandidateAnswer] | None,
) -> set[str]:
    return {
        answer.requirement_id
        for answer in (candidate_answers or [])
        if answer.requirement_id and answer.yes_no is False
    }


def _recommended_evidence_action(
    status: str,
    represented: bool,
    acknowledged_no: bool = False,
) -> str:
    if status == "supported" and represented:
        return "Keep the wording concise and preserve the verified evidence."
    if status == "supported":
        return "Consider emphasizing this verified requirement in the proposed resume."
    if status == "partial" and represented:
        return "Use cautious wording and ask the candidate to confirm the remaining scope."
    if status == "partial":
        return "Ask the candidate for confirmation before adding or strengthening this requirement."
    if status == "unsupported":
        if acknowledged_no:
            return "Keep this as an acknowledged gap and do not add it to the resume."
        return "Do not add this claim unless the candidate provides verifiable evidence."
    return "Review this requirement and assign an evidence decision before export."


def build_evidence_gap_report(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None = None,
) -> tuple[EvidenceGapSummary, list[EvidenceGapRow]]:
    """Build the scored evidence matrix displayed in initial and updated reports."""
    evidence_lookup = {item.requirement_id: item for item in proposal.evidence_matches}
    location_lookup = _evidence_location_lookup(profile)
    negative_requirement_ids = _negative_answer_requirement_ids(candidate_answers)
    rows: list[EvidenceGapRow] = []
    supported = 0
    partial = 0
    unsupported = 0

    for requirement in analysis.requirements:
        match = evidence_lookup.get(requirement.id)
        status = match.status if match else "no decision"
        represented = _requirement_is_represented(requirement, proposal)
        acknowledged_no = requirement.id in negative_requirement_ids
        report_status, score, _ = _evidence_requirement_result(
            status,
            represented,
            acknowledged_no,
        )
        if status == "supported":
            supported += 1
        elif status == "partial":
            partial += 1
        else:
            unsupported += 1

        evidence_locations = [
            location_lookup.get(evidence_id, evidence_id)
            for evidence_id in (match.evidence_ids if match else [])
        ]
        rows.append(
            EvidenceGapRow(
                requirement_id=requirement.id,
                priority=requirement.priority,
                category=requirement.category,
                requirement=requirement.requirement,
                evidence_status=status,
                appears_in_resume=represented,
                evidence_locations=evidence_locations,
                rationale=match.rationale if match else "No evidence decision was supplied.",
                recommended_action=_recommended_evidence_action(
                    status,
                    represented,
                    acknowledged_no,
                ),
                score=score,
                report_status=report_status,
            )
        )

    summary = EvidenceGapSummary(
        supported=supported,
        partial=partial,
        unsupported=unsupported,
        candidate_confirmations=(
            len(candidate_answers)
            if candidate_answers is not None
            else len(proposal.candidate_questions)
        ),
    )
    return summary, rows


def _evidence_gaps_section(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None,
) -> ReportSection:
    _, rows = build_evidence_gap_report(
        profile,
        analysis,
        proposal,
        candidate_answers,
    )
    checks: list[ReportCheck] = []
    for row in rows:
        _, _, score_detail = _evidence_requirement_result(
            row.evidence_status,
            row.appears_in_resume,
            row.requirement_id in _negative_answer_requirement_ids(candidate_answers),
        )
        checks.append(
            ReportCheck(
                row.requirement,
                row.report_status,
                f"{row.priority.capitalize()} requirement. {score_detail}",
                weight=_EVIDENCE_PRIORITY_WEIGHTS.get(row.priority, 1.0),
                score_value=row.score,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "No analyzed requirements",
                "info",
                "The job analysis did not return requirements for evidence scoring.",
            )
        )

    return ReportSection(
        "Evidence & Gaps",
        "This section measures whether job requirements are supported by verified candidate evidence and represented truthfully in the resume. Critical requirements receive more weight than important or secondary requirements.",
        [ReportSubsection("Requirement evidence coverage", checks)],
    )




def _education_match(profile: CandidateProfile, analysis: JobAnalysis) -> ReportCheck:
    education_requirements = [
        requirement
        for requirement in analysis.requirements
        if requirement.category == "qualification"
        and re.search(
            r"\b(?:degree|bachelor|bachelor's|master|master's|phd|doctorate|education|college|university)\b",
            requirement.requirement,
            re.IGNORECASE,
        )
    ]
    if not education_requirements:
        return ReportCheck(
            "Education matches the job description",
            "pass",
            "No explicit required or preferred degree was identified in the job description, so no education mismatch was found.",
        )

    education_text = _normalize(
        " ".join(
            f"{item.credential} {item.institution} {item.detail}"
            for item in profile.education
        )
    )
    failures: list[str] = []
    for requirement in education_requirements:
        normalized_requirement = _normalize(requirement.requirement)
        requires_master = bool(re.search(r"\bmaster", normalized_requirement))
        requires_bachelor = bool(re.search(r"\bbachelor|\bdegree|\bcollege|\buniversity", normalized_requirement))
        has_master = "master" in education_text or "m s" in education_text
        has_bachelor = "bachelor" in education_text or "b s" in education_text or has_master
        if requires_master and not has_master:
            failures.append(requirement.requirement)
        elif requires_bachelor and not has_bachelor:
            failures.append(requirement.requirement)

    if failures:
        return ReportCheck(
            "Education matches the job description",
            "fail",
            "The profile does not clearly satisfy: " + "; ".join(failures),
        )
    return ReportCheck(
        "Education matches the job description",
        "pass",
        "The candidate's verified education satisfies the degree requirements identified in the job description.",
    )


def _status_for_score(score: float, *, pass_at: float = 80.0, warning_at: float = 50.0) -> ReportStatus:
    if score >= pass_at:
        return "pass"
    if score >= warning_at:
        return "warning"
    return "fail"


def _semantic_match_subsection(
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> ReportSubsection:
    evidence_by_requirement = {
        item.requirement_id: item for item in proposal.evidence_matches
    }
    priority_weights = {"critical": 3.0, "important": 2.0, "secondary": 1.0}
    checks: list[ReportCheck] = []

    for requirement in analysis.requirements:
        evidence = evidence_by_requirement.get(requirement.id)
        evidence_status = evidence.status if evidence else "unsupported"
        represented = _requirement_is_represented(requirement, proposal)
        evidence_score = {"supported": 70.0, "partial": 45.0, "unsupported": 0.0}[evidence_status]
        representation_score = 30.0 if represented else 0.0
        score = min(100.0, evidence_score + representation_score)
        status = _status_for_score(score)
        if evidence_status == "supported" and represented:
            interpretation = "Verified experience supports the meaning of this requirement and the current resume represents it."
        elif evidence_status == "supported":
            interpretation = "Verified experience supports this requirement, but the meaning is not clearly represented in the current resume."
        elif evidence_status == "partial" and represented:
            interpretation = "The resume addresses this requirement, but the verified evidence supports only part of it."
        elif evidence_status == "partial":
            interpretation = "Only partial verified evidence exists, and the current resume does not clearly represent it."
        else:
            interpretation = "No verified experience currently supports this requirement, so it should remain a disclosed gap."

        checks.append(
            ReportCheck(
                f'{requirement.id} semantic match · {requirement.requirement}',
                status,
                f"Meaning-based score: {score:.0f}%. Evidence: {evidence_status}. Represented in resume: {'yes' if represented else 'no'}. {interpretation}",
                weight=priority_weights.get(requirement.priority, 1.0),
                score_value=score,
            )
        )

    if not checks:
        checks.append(
            ReportCheck(
                "Meaning-based requirement coverage can be calculated",
                "warning",
                "No job requirements were available for semantic comparison.",
            )
        )
    return ReportSubsection("Semantic Match", checks)


def _document_section_positions(document: Document | None) -> dict[str, int]:
    if document is None:
        return {}
    paragraphs = [paragraph.text.strip().casefold() for paragraph in _body_paragraphs(document)]
    positions: dict[str, int] = {}
    for key, aliases in _SECTION_HEADING_ALIASES:
        for index, text in enumerate(paragraphs):
            if any(text == alias or text.startswith(alias + " ") for alias in aliases):
                positions[key] = index
                break
    return positions


def _data_structure_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    document: Document | None,
    inspection_note: str | None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
) -> ReportSubsection:
    missing_entities: list[str] = []
    if not profile.name.strip():
        missing_entities.append("candidate name")
    if not profile.contact.email.strip():
        missing_entities.append("email")
    if not profile.contact.phone.strip():
        missing_entities.append("phone")
    if not profile.contact.location.strip():
        missing_entities.append("location")
    for index, experience in enumerate(profile.experiences, start=1):
        if not experience.employer.strip():
            missing_entities.append(f"experience {index} employer")
        if not experience.title.strip():
            missing_entities.append(f"experience {index} title")
        if not experience.dates.strip():
            missing_entities.append(f"experience {index} dates")
        if not experience.bullets:
            missing_entities.append(f"experience {index} accomplishments")
    for index, education in enumerate(profile.education, start=1):
        if not education.credential.strip():
            missing_entities.append(f"education {index} credential")
        if not education.institution.strip():
            missing_entities.append(f"education {index} institution")
        if not education.date.strip():
            missing_entities.append(f"education {index} date")

    source_ids = set(profile.bullet_lookup())
    proposal_ids = {item.source_bullet_id for item in proposal.bullet_proposals}
    unmapped_source_ids = sorted(source_ids - proposal_ids)
    unknown_proposal_ids = sorted(proposal_ids - source_ids)

    positions = _document_section_positions(document)
    stage = normalize_career_stage(career_stage)
    format_key = normalize_resume_format(resume_format)
    if format_key == "technical":
        expected_order = ["skills", "summary", "experience", "education"]
    elif format_key == "career_changer":
        expected_order = ["summary", "skills", "education", "experience"]
    elif format_key == "freelance":
        expected_order = ["summary", "skills", "experience", "education"]
    elif stage == "early_career":
        expected_order = ["summary", "skills", "education", "experience"]
    else:
        expected_order = ["summary", "skills", "experience", "education"]
    missing_sections = [name for name in expected_order if name not in positions]
    order_is_valid = not missing_sections and [positions[name] for name in expected_order] == sorted(
        positions[name] for name in expected_order
    )

    document_text = _document_text(document).casefold() if document is not None else ""
    key_entities = [profile.name, profile.contact.email, profile.contact.phone]
    missing_from_document = [
        value for value in key_entities if value.strip() and value.strip().casefold() not in document_text
    ]

    return ReportSubsection(
        "Data & Structure",
        [
            ReportCheck(
                "Core resume entities are complete",
                "pass" if not missing_entities else "fail",
                "The structured profile includes the candidate name, contact details, complete work-history entities, and complete education entities."
                if not missing_entities
                else "Missing or incomplete entities: " + "; ".join(missing_entities[:12]) + ".",
            ),
            ReportCheck(
                "Every source accomplishment maps to the resume workflow",
                "pass" if not unmapped_source_ids and not unknown_proposal_ids else "fail",
                "Every source bullet has a corresponding proposal record and no unknown source IDs were introduced."
                if not unmapped_source_ids and not unknown_proposal_ids
                else "Mapping problems — missing proposal IDs: "
                + (", ".join(unmapped_source_ids) or "none")
                + "; unknown proposal IDs: "
                + (", ".join(unknown_proposal_ids) or "none")
                + ".",
            ),
            ReportCheck(
                "The generated resume preserves the expected section hierarchy",
                "pass" if order_is_valid else "warning" if document is None else "fail",
                "The generated resume sections match the selected career stage and resume format."
                if order_is_valid
                else (inspection_note or "The generated document was unavailable for structural inspection.")
                if document is None
                else "Missing or out-of-order sections: " + ", ".join(missing_sections or expected_order) + ".",
            ),
            ReportCheck(
                "Key extracted entities appear in the generated document",
                "pass" if document is not None and not missing_from_document else "warning" if document is None else "fail",
                "The candidate name, email, and phone number were found in the generated document."
                if document is not None and not missing_from_document
                else (inspection_note or "The generated document was unavailable for entity verification.")
                if document is None
                else "These profile entities were not found in the generated document: " + ", ".join(missing_from_document) + ".",
            ),
        ],
    )


def _language_quality_subsection(summary: str, bullets: list[str]) -> ReportSubsection:
    text_blocks = [summary.strip(), *[bullet.strip() for bullet in bullets if bullet.strip()]]
    combined = "\n".join(block for block in text_blocks if block)
    normalized_words = [word.casefold() for word in _words(combined)]
    misspellings = sorted(
        {word for word in normalized_words if word in _COMMON_MISSPELLINGS}
    )
    repeated_words = sorted(
        {word.casefold() for word in adjacent_repeated_words(combined)}
    )
    malformed_punctuation: list[str] = []
    if re.search(r"\s+[,.!?;:]", combined):
        malformed_punctuation.append("spaces before punctuation")
    if re.search(r"[!?.,;:]{2,}", combined):
        malformed_punctuation.append("repeated punctuation")
    if re.search(r"\s{2,}", combined):
        malformed_punctuation.append("repeated spaces")
    bracket_pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    unbalanced = [f"{left}{right}" for left, right in bracket_pairs if combined.count(left) != combined.count(right)]

    lowercase_starts = [
        block[:45]
        for block in text_blocks
        if block and block[0].isalpha() and block[0].islower()
    ]
    language_issues = len(misspellings) + len(repeated_words) + len(malformed_punctuation) + len(unbalanced)
    grammar_status: ReportStatus = "pass" if language_issues == 0 else "warning" if language_issues <= 2 else "fail"
    grammar_details: list[str] = []
    if misspellings:
        grammar_details.append(
            "possible misspellings: "
            + ", ".join(f"{word} → {_COMMON_MISSPELLINGS[word]}" for word in misspellings)
        )
    if repeated_words:
        grammar_details.append("repeated words: " + ", ".join(repeated_words))
    if malformed_punctuation:
        grammar_details.append("formatting issues: " + ", ".join(malformed_punctuation))
    if unbalanced:
        grammar_details.append("unbalanced brackets: " + ", ".join(unbalanced))

    return ReportSubsection(
        "Grammar & Spelling",
        [
            ReportCheck(
                "No common spelling, repeated-word, or punctuation errors were detected",
                grammar_status,
                "The deterministic language scan found no common spelling, repeated-word, spacing, punctuation, or bracket issues."
                if not grammar_details
                else "The language scan found " + "; ".join(grammar_details) + ". Review context before accepting a correction.",
            ),
            ReportCheck(
                "Summary and bullets begin with consistent capitalization",
                "pass" if not lowercase_starts else "warning",
                "The summary and selected bullets begin with consistent capitalization."
                if not lowercase_starts
                else "These entries begin with lowercase text: " + "; ".join(lowercase_starts[:6]) + ".",
            ),
        ],
    )


def _normalize_number_token(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold().replace(",", ""))


def _metric_quality_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    candidate_answers: list[CandidateAnswer] | None,
) -> ReportSubsection:
    source_lookup = profile.bullet_lookup()
    supplemental_text = " ".join(item.statement for item in profile.supplemental_evidence)
    answer_text = " ".join(answer.text for answer in (candidate_answers or []) if answer.text.strip())
    unsupported_metrics: list[str] = []
    suspicious_metrics: list[str] = []
    formatting_issues: list[str] = []

    metric_items = [
        (
            "professional summary",
            proposal.professional_summary,
            profile.current_summary + " " + supplemental_text + " " + answer_text,
        )
    ]
    metric_items.extend(
        (
            bullet.source_bullet_id,
            bullet.proposed_text,
            source_lookup.get(bullet.source_bullet_id, "") + " " + supplemental_text + " " + answer_text,
        )
        for bullet in proposal.bullet_proposals
        if bullet.include
    )

    for source_id, proposed_text, verified_text in metric_items:
        verified_tokens = {
            _normalize_number_token(match.group(0))
            for match in _NUMBER_TOKEN_PATTERN.finditer(verified_text)
        }
        for match in _NUMBER_TOKEN_PATTERN.finditer(proposed_text):
            raw = match.group(0)
            normalized = _normalize_number_token(raw)
            if normalized not in verified_tokens:
                unsupported_metrics.append(f"{source_id}: {raw}")
            numeric_match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
            if numeric_match:
                value = float(numeric_match.group(0).replace(",", ""))
                if "%" in raw and value > 1000:
                    suspicious_metrics.append(f"{source_id}: {raw}")
            if re.search(r"[$€£]\s+\d", raw):
                formatting_issues.append(f"space after currency symbol in {raw}")
            if re.search(r"\d\s+%", raw):
                formatting_issues.append(f"space before percent sign in {raw}")

        for range_match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\b", proposed_text):
            if float(range_match.group(1)) > float(range_match.group(2)):
                suspicious_metrics.append(
                    f"{source_id}: descending range {range_match.group(0)}"
                )

    unsupported_metrics = sorted(set(unsupported_metrics))
    suspicious_metrics = sorted(set(suspicious_metrics))
    formatting_issues = sorted(set(formatting_issues))
    return ReportSubsection(
        "Metric Integrity",
        [
            ReportCheck(
                "Every numeric claim is traceable to verified source evidence",
                "pass" if not unsupported_metrics else "fail",
                "All numbers, percentages, and monetary values in selected bullets also appear in the verified profile or candidate confirmations."
                if not unsupported_metrics
                else "Potentially unsupported metrics: " + "; ".join(unsupported_metrics[:12]) + ". Remove them or confirm the evidence.",
            ),
            ReportCheck(
                "Metrics are logically plausible",
                "pass" if not suspicious_metrics else "warning",
                "No obviously implausible percentages or descending numerical ranges were detected."
                if not suspicious_metrics
                else "Review these potentially implausible metrics: " + "; ".join(suspicious_metrics[:10]) + ".",
            ),
            ReportCheck(
                "Metric formatting is consistent",
                "pass" if not formatting_issues else "warning",
                "Currency and percentage symbols use consistent compact formatting."
                if not formatting_issues
                else "Formatting inconsistencies: " + "; ".join(formatting_issues[:10]) + ".",
            ),
        ],
    )


def _syllable_count(word: str) -> int:
    cleaned = re.sub(r"[^a-z]", "", word.casefold())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1
    groups = re.findall(r"[aeiouy]+", cleaned)
    count = len(groups)
    if cleaned.endswith("e") and not cleaned.endswith(("le", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def _readability_subsection(summary: str, bullets: list[str]) -> ReportSubsection:
    blocks = [summary.strip(), *[bullet.strip() for bullet in bullets if bullet.strip()]]
    words = [word for block in blocks for word in _words(block)]
    sentence_count = max(1, sum(max(1, len(re.findall(r"[.!?]+", block))) for block in blocks if block))
    syllables = sum(_syllable_count(word) for word in words)
    word_count = max(1, len(words))
    reading_ease = 206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllables / word_count)
    grade_level = 0.39 * (word_count / sentence_count) + 11.8 * (syllables / word_count) - 15.59
    reading_ease = max(0.0, min(100.0, reading_ease))
    grade_level = max(0.0, grade_level)
    readability_score = max(0.0, min(100.0, 100.0 - max(0.0, grade_level - 10.0) * 8.0))
    jargon_words = sorted(
        {
            word
            for word in words
            if len(word) >= 13 and _syllable_count(word) >= 4
        },
        key=lambda value: (-len(value), value.casefold()),
    )
    jargon_ratio = len([word for word in words if len(word) >= 13 and _syllable_count(word) >= 4]) / word_count

    return ReportSubsection(
        "Readability",
        [
            ReportCheck(
                "The resume has a recruiter-friendly readability level",
                _status_for_score(readability_score, pass_at=70.0, warning_at=45.0),
                f"Estimated Flesch reading ease: {reading_ease:.1f}; estimated grade level: {grade_level:.1f}. Technical resumes can be specialized, but sentences should remain direct and scannable.",
                score_value=readability_score,
            ),
            ReportCheck(
                "Jargon density is controlled",
                "pass" if jargon_ratio <= 0.08 else "warning" if jargon_ratio <= 0.14 else "fail",
                f"Approximately {jargon_ratio:.0%} of words are long, complex terms."
                + (" The density is reasonable for a technical resume." if jargon_ratio <= 0.08 else " Consider simplifying or defining terms such as " + ", ".join(jargon_words[:8]) + "."),
            ),
        ],
    )


def _writing_style_subsection(
    profile: CandidateProfile,
    proposal: TailoringProposal,
) -> ReportSubsection:
    selected = [item for item in proposal.bullet_proposals if item.include and item.proposed_text.strip()]
    personal_pronouns = sorted(
        {match.group(0).casefold() for item in selected for match in _PERSONAL_PRONOUN_PATTERN.finditer(item.proposed_text)}
        | {match.group(0).casefold() for match in _PERSONAL_PRONOUN_PATTERN.finditer(proposal.professional_summary)}
    )
    punctuation_endings = [item.proposed_text.strip()[-1] in ".;" for item in selected]
    punctuation_consistent = not punctuation_endings or all(punctuation_endings) or not any(punctuation_endings)
    non_action_openers: list[str] = []
    tense_issues: list[str] = []
    experience_by_bullet = {
        bullet.id: experience
        for experience in profile.experiences
        for bullet in experience.bullets
    }
    for item in selected:
        words = _words(item.proposed_text)
        if not words:
            continue
        opener = words[0].casefold()
        if opener not in _ACTION_VERBS and not opener.endswith("ed") and opener not in _IRREGULAR_PAST_OPENERS:
            non_action_openers.append(f"{item.source_bullet_id}: {words[0]}")
        experience = experience_by_bullet.get(item.source_bullet_id)
        if experience and not re.search(r"\b(?:present|current)\b", experience.dates, re.IGNORECASE):
            if opener in _ACTION_VERBS and not opener.endswith("ed") and opener not in _IRREGULAR_PAST_OPENERS:
                tense_issues.append(f"{item.source_bullet_id}: {words[0]}")

    action_ratio = 1.0 - (len(non_action_openers) / max(1, len(selected)))
    return ReportSubsection(
        "Writing Style",
        [
            ReportCheck(
                "The resume avoids personal pronouns",
                "pass" if not personal_pronouns else "warning",
                "No first-person personal pronouns were detected."
                if not personal_pronouns
                else "Remove personal pronouns such as: " + ", ".join(personal_pronouns) + ".",
            ),
            ReportCheck(
                "Bullet punctuation is consistent",
                "pass" if punctuation_consistent else "warning",
                "Selected bullets consistently use or omit ending punctuation."
                if punctuation_consistent
                else "Some bullets end with punctuation while others do not. Use one convention throughout.",
            ),
            ReportCheck(
                "Bullets use a parallel action-led structure",
                "pass" if action_ratio >= 0.8 else "warning" if action_ratio >= 0.6 else "fail",
                f"{len(selected) - len(non_action_openers)} of {len(selected)} selected bullets begin with a recognized action-oriented verb."
                + ("" if not non_action_openers else " Review: " + "; ".join(non_action_openers[:8]) + "."),
                score_value=action_ratio * 100.0,
            ),
            ReportCheck(
                "Past roles use consistent past-tense openings",
                "pass" if not tense_issues else "warning",
                "No clear tense inconsistencies were detected in completed roles."
                if not tense_issues
                else "Review possible present-tense openings in completed roles: " + "; ".join(tense_issues[:8]) + ".",
            ),
        ],
    )


def _content_focus_subsection(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
) -> ReportSubsection:
    selected = [item for item in proposal.bullet_proposals if item.include and item.proposed_text.strip()]
    long_bullets = [
        f"{item.source_bullet_id} ({len(_words(item.proposed_text))} words)"
        for item in selected
        if len(_words(item.proposed_text)) > 35
    ]
    normalized_counts = Counter(_normalize(item.proposed_text) for item in selected)
    duplicates = [text for text, count in normalized_counts.items() if text and count > 1]
    evidence_by_id = {item.requirement_id: item for item in proposal.evidence_matches}
    supported_not_represented = [
        requirement.requirement
        for requirement in analysis.requirements
        if requirement.priority in {"critical", "important"}
        and evidence_by_id.get(requirement.id)
        and evidence_by_id[requirement.id].status in {"supported", "partial"}
        and not _requirement_is_represented(requirement, proposal)
    ]
    selected_ids = {item.source_bullet_id for item in selected}
    older_role_warnings: list[str] = []
    current_month = date.today().year * 12 + date.today().month - 1
    for experience in profile.experiences:
        parts = re.split(r"\s*[-–—]\s*", experience.dates.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        end = _month_index(parts[1])
        selected_count = sum(bullet.id in selected_ids for bullet in experience.bullets)
        if end is not None and current_month - end >= 120 and selected_count > 3:
            older_role_warnings.append(f"{experience.employer}: {selected_count} bullets")

    return ReportSubsection(
        "Content Focus",
        [
            ReportCheck(
                "Supported priority requirements are represented without inventing experience",
                "pass" if not supported_not_represented else "warning",
                "All supported critical and important requirements are represented in the resume."
                if not supported_not_represented
                else "Consider safely augmenting existing evidence for: " + "; ".join(supported_not_represented[:10]) + ".",
            ),
            ReportCheck(
                "Bullets are concise enough to scan quickly",
                "pass" if not long_bullets else "warning",
                "Every selected bullet contains 35 words or fewer."
                if not long_bullets
                else "Shorten these bullets: " + "; ".join(long_bullets[:10]) + ".",
            ),
            ReportCheck(
                "The resume does not repeat identical accomplishment bullets",
                "pass" if not duplicates else "fail",
                "No duplicate selected bullets were detected."
                if not duplicates
                else f"{len(duplicates)} duplicated bullet text pattern(s) were detected and should be pruned.",
            ),
            ReportCheck(
                "Older roles are proportionately concise",
                "pass" if not older_role_warnings else "warning",
                "Roles that ended at least 10 years ago use no more than three selected bullets."
                if not older_role_warnings
                else "Consider pruning older roles: " + "; ".join(older_role_warnings) + ".",
            ),
        ],
    )


def _content_quality_section(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    selected_bullets: list[str],
    document: Document | None,
    inspection_note: str | None,
    candidate_answers: list[CandidateAnswer] | None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
) -> ReportSection:
    return ReportSection(
        "Content Quality",
        "These checks validate the transformed resume beyond exact keywords: structured data integrity, meaning-based job alignment, grammar and spelling, metric credibility, readability, consistent writing style, and focused content.",
        [
            _data_structure_subsection(
                profile,
                proposal,
                document,
                inspection_note,
                career_stage=career_stage,
                resume_format=resume_format,
            ),
            _semantic_match_subsection(analysis, proposal),
            _language_quality_subsection(proposal.professional_summary, selected_bullets),
            _metric_quality_subsection(profile, proposal, candidate_answers),
            _readability_subsection(proposal.professional_summary, selected_bullets),
            _writing_style_subsection(profile, proposal),
            _content_focus_subsection(profile, analysis, proposal),
        ],
    )


def _approved_resume_for_report(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    resume_title: str,
) -> ApprovedResume:
    proposal_lookup = {item.source_bullet_id: item for item in proposal.bullet_proposals}
    bullets_by_experience: dict[str, list[str]] = {}
    for experience in profile.experiences:
        bullets_by_experience[experience.id] = [
            proposal_lookup[bullet.id].proposed_text.strip()
            for bullet in experience.bullets
            if bullet.id in proposal_lookup
            and proposal_lookup[bullet.id].include
            and proposal_lookup[bullet.id].proposed_text.strip()
        ]
    return ApprovedResume(
        target_title=resume_title,
        professional_summary=proposal.professional_summary,
        skills=proposal.skills,
        bullets_by_experience=bullets_by_experience,
    )


def _document_for_report(
    template_path: str | Path | None,
    profile: CandidateProfile,
    proposal: TailoringProposal,
    resume_title: str,
    generated_document_bytes: bytes | None = None,
    *,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
) -> tuple[Document | None, str | None]:
    if generated_document_bytes is not None:
        try:
            return Document(BytesIO(generated_document_bytes)), None
        except Exception as generated_error:
            return None, f"The generated resume document could not be inspected: {generated_error}"
    if not template_path:
        return None, "No resume template was supplied for formatting inspection."
    try:
        approved = _approved_resume_for_report(profile, proposal, resume_title)
        generated = export_resume_docx(
            template_path,
            profile,
            approved,
            enforce_language_gate=False,
            career_stage=career_stage,
            resume_format=resume_format,
            visual_design=visual_design,
        )
        return Document(BytesIO(generated)), None
    except Exception as generated_error:
        try:
            return Document(str(template_path)), (
                "The current proposal could not be rendered into the template, so template formatting "
                f"was inspected instead: {generated_error}"
            )
        except Exception as template_error:  # pragma: no cover - defensive UI fallback
            return None, f"The resume document could not be inspected: {template_error}"


def _body_paragraphs(document: Document) -> list:
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    return paragraphs


def _header_footer_parts(document: Document) -> list[HeaderPart | FooterPart]:
    return [
        part
        for part in document.part.package.parts
        if isinstance(part, (HeaderPart, FooterPart))
    ]


def _part_text(part: HeaderPart | FooterPart) -> str:
    return " ".join(
        node.text.strip()
        for node in part.element.xpath(".//w:t")
        if node.text and node.text.strip()
    )


def _column_count(section) -> int:
    columns = section._sectPr.find(qn("w:cols"))
    if columns is None:
        return 1
    declared = columns.get(qn("w:num"))
    declared_count = int(declared) if declared and declared.isdigit() else 1
    explicit_count = len(columns.findall(qn("w:col")))
    return max(1, declared_count, explicit_count)


def _paragraph_alignment(paragraph):
    if paragraph.alignment is not None:
        return paragraph.alignment
    style = paragraph.style
    while style is not None:
        if style.paragraph_format.alignment is not None:
            return style.paragraph_format.alignment
        style = style.base_style
    return WD_ALIGN_PARAGRAPH.LEFT


def _style_chain_font_value(style, attribute: str):
    while style is not None:
        value = getattr(style.font, attribute)
        if value is not None:
            return value
        style = style.base_style
    return None


def _resolved_font_name(document: Document, paragraph, run) -> str:
    name = run.font.name
    if not name:
        name = _style_chain_font_value(run.style, "name")
    if not name:
        name = _style_chain_font_value(paragraph.style, "name")
    if not name:
        name = _style_chain_font_value(document.styles["Normal"], "name")
    return (name or "Unknown").strip()


def _resolved_font_size(document: Document, paragraph, run) -> float | None:
    size = run.font.size
    if size is None:
        size = _style_chain_font_value(run.style, "size")
    if size is None:
        size = _style_chain_font_value(paragraph.style, "size")
    if size is None:
        size = _style_chain_font_value(document.styles["Normal"], "size")
    return size.pt if size is not None else None


def _resolved_bold(paragraph, run) -> bool:
    value = run.bold
    if value is None:
        value = _style_chain_font_value(run.style, "bold")
    if value is None:
        value = _style_chain_font_value(paragraph.style, "bold")
    return bool(value)


def _resolved_rgb(paragraph, run) -> str:
    color = run.font.color.rgb
    if color is None:
        style = run.style
        while style is not None and color is None:
            color = style.font.color.rgb
            style = style.base_style
    if color is None:
        style = paragraph.style
        while style is not None and color is None:
            color = style.font.color.rgb
            style = style.base_style
    return str(color).upper() if color is not None else "000000"


def _relative_luminance(rgb: str) -> float:
    channels = [int(rgb[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_with_white(rgb: str) -> float:
    return 1.05 / (_relative_luminance(rgb) + 0.05)


def _document_text(document: Document) -> str:
    parts = [paragraph.text for paragraph in _body_paragraphs(document)]
    parts.extend(_part_text(part) for part in _header_footer_parts(document))
    return "\n".join(part for part in parts if part)




def _paragraph_format_value(paragraph, attribute: str):
    value = getattr(paragraph.paragraph_format, attribute)
    if value is not None:
        return value
    style = paragraph.style
    while style is not None:
        value = getattr(style.paragraph_format, attribute)
        if value is not None:
            return value
        style = style.base_style
    return None


def _length_points(value) -> float:
    return round(value.pt, 2) if value is not None and hasattr(value, "pt") else 0.0


def _list_signature(paragraph) -> tuple[str, str, str]:
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    num_id = ""
    level = ""
    if num_pr is not None:
        if num_pr.numId is not None:
            num_id = str(num_pr.numId.val)
        if num_pr.ilvl is not None:
            level = str(num_pr.ilvl.val)
    return paragraph.style.style_id or paragraph.style.name, num_id, level


def _is_list_paragraph(paragraph) -> bool:
    style_text = f"{paragraph.style.style_id} {paragraph.style.name}".casefold()
    p_pr = paragraph._p.pPr
    return "list" in style_text or "bullet" in style_text or bool(p_pr is not None and p_pr.numPr is not None)


def _estimated_page_count(document: Document) -> int:
    estimated_pages = 0
    remaining_lines = 0.0
    for section in document.sections:
        page_height = section.page_height.inches if section.page_height is not None else 11.0
        top = section.top_margin.inches if section.top_margin is not None else 0.75
        bottom = section.bottom_margin.inches if section.bottom_margin is not None else 0.75
        usable_height = max(4.0, page_height - top - bottom)
        remaining_lines += usable_height * 72.0 / 13.0

    page_width = document.sections[0].page_width.inches if document.sections and document.sections[0].page_width is not None else 8.5
    left = document.sections[0].left_margin.inches if document.sections and document.sections[0].left_margin is not None else 0.75
    right = document.sections[0].right_margin.inches if document.sections and document.sections[0].right_margin is not None else 0.75
    usable_width = max(4.0, page_width - left - right)
    chars_per_line = max(45, int(usable_width * 9.5))
    consumed_lines = 0.0
    for paragraph in _body_paragraphs(document):
        text = paragraph.text.strip()
        if not text:
            consumed_lines += 0.35
            continue
        line_count = max(1, ceil(len(text) / chars_per_line))
        before = _length_points(_paragraph_format_value(paragraph, "space_before")) / 13.0
        after = _length_points(_paragraph_format_value(paragraph, "space_after")) / 13.0
        consumed_lines += line_count + before + after
    if remaining_lines <= 0:
        return 1
    estimated_pages = max(1, ceil(consumed_lines / (remaining_lines / max(1, len(document.sections)))))
    return estimated_pages


def _rendered_page_count(
    document: Document,
    *,
    exact: bool = True,
) -> tuple[int, str]:
    if not exact:
        return _estimated_page_count(document), "estimated"
    renderer = shutil.which("libreoffice") or shutil.which("soffice")
    if renderer:
        try:
            with tempfile.TemporaryDirectory(prefix="resume-report-pages-") as temp_dir:
                docx_path = Path(temp_dir) / "resume.docx"
                pdf_path = Path(temp_dir) / "resume.pdf"
                document.save(docx_path)
                # Give every conversion its own LibreOffice user profile. Reusing the
                # default profile can leave a lock after repeated report generation,
                # causing later requests to wait until the subprocess timeout.
                profile_dir = Path(temp_dir) / "libreoffice-profile"
                profile_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        renderer,
                        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nofirststartwizard",
                        "--nolockcheck",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temp_dir,
                        str(docx_path),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                )
                if pdf_path.exists():
                    pdf_bytes = pdf_path.read_bytes()
                    page_count = len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))
                    if page_count > 0:
                        return page_count, "rendered"
        except Exception as exc:  # Renderer availability must not block report generation.
            logger.debug("Exact page rendering failed; using the estimated page count: %s", exc)
    return _estimated_page_count(document), "estimated"


def _typography_checks(document: Document) -> list[ReportCheck]:
    paragraphs = [paragraph for paragraph in _body_paragraphs(document) if paragraph.text.strip()]
    list_paragraphs = [paragraph for paragraph in paragraphs if _is_list_paragraph(paragraph)]
    list_signatures = Counter(_list_signature(paragraph) for paragraph in list_paragraphs)
    indentation_signatures = Counter(
        (
            _length_points(_paragraph_format_value(paragraph, "left_indent")),
            _length_points(_paragraph_format_value(paragraph, "right_indent")),
            _length_points(_paragraph_format_value(paragraph, "first_line_indent")),
        )
        for paragraph in list_paragraphs
    )
    body_paragraphs = [paragraph for paragraph in paragraphs if len(_words(paragraph.text)) >= 4]
    line_spacing_signatures = Counter(
        str(_paragraph_format_value(paragraph, "line_spacing") or "default")
        for paragraph in body_paragraphs
    )
    paragraph_spacing_signatures = Counter(
        (
            _length_points(_paragraph_format_value(paragraph, "space_before")),
            _length_points(_paragraph_format_value(paragraph, "space_after")),
        )
        for paragraph in body_paragraphs
    )

    headings = []
    for paragraph in paragraphs:
        normalized = paragraph.text.strip().casefold()
        if any(normalized == alias for _, aliases in _SECTION_HEADING_ALIASES for alias in aliases):
            run = next((item for item in paragraph.runs if item.text.strip()), None)
            headings.append(
                (
                    paragraph.style.style_id,
                    _resolved_font_name(document, paragraph, run) if run is not None else "Unknown",
                    _resolved_font_size(document, paragraph, run) if run is not None else None,
                    _resolved_bold(paragraph, run) if run is not None else False,
                    _length_points(_paragraph_format_value(paragraph, "space_before")),
                    _length_points(_paragraph_format_value(paragraph, "space_after")),
                )
            )
    heading_consistent = len(set(headings)) <= 1 if headings else False

    return [
        ReportCheck(
            "Bullet and list styles are consistent",
            "pass" if len(list_signatures) <= 1 else "warning",
            "All detected list paragraphs use the same bullet/list style."
            if len(list_signatures) <= 1
            else f"{len(list_signatures)} different list-style signatures were detected. Standardize bullet symbols and nesting levels.",
        ),
        ReportCheck(
            "Bullet indentation is consistent",
            "pass" if len(indentation_signatures) <= 1 else "warning",
            "All detected bullets use consistent indentation."
            if len(indentation_signatures) <= 1
            else f"{len(indentation_signatures)} bullet indentation patterns were detected. Align bullet left indents and hanging indents.",
        ),
        ReportCheck(
            "Line spacing is consistent throughout the resume",
            "pass" if len(line_spacing_signatures) <= 2 else "warning",
            f"{len(line_spacing_signatures)} line-spacing pattern(s) were detected across body text."
            + ("" if len(line_spacing_signatures) <= 2 else " Reduce unnecessary variation."),
        ),
        ReportCheck(
            "Paragraph spacing is consistent throughout the resume",
            "pass" if len(paragraph_spacing_signatures) <= 3 else "warning",
            f"{len(paragraph_spacing_signatures)} paragraph-spacing pattern(s) were detected across body text."
            + ("" if len(paragraph_spacing_signatures) <= 3 else " Standardize spacing before and after equivalent content."),
        ),
        ReportCheck(
            "Section-heading typography is consistent",
            "pass" if heading_consistent else "warning",
            "Detected section headings use a consistent font, size, emphasis, style, and spacing."
            if heading_consistent
            else "Section headings use inconsistent typography or could not all be identified. Standardize heading font, size, bolding, and spacing.",
        ),
    ]


def _formatting_sections(
    document: Document | None,
    inspection_note: str | None,
    page_limit: int,
    *,
    exact_page_count: bool = True,
) -> list[ReportSubsection]:
    if document is None:
        unavailable = ReportCheck(
            "The resume document can be inspected",
            "warning",
            inspection_note or "The document was unavailable for inspection.",
        )
        return [
            ReportSubsection("Layout", [unavailable]),
            ReportSubsection("Font Check", [unavailable]),
            ReportSubsection("Typography Consistency", [unavailable]),
            ReportSubsection("Page Setup", [unavailable]),
        ]

    paragraphs = [paragraph for paragraph in _body_paragraphs(document) if paragraph.text.strip()]
    full_text = _document_text(document)

    column_counts = [_column_count(section) for section in document.sections]
    multi_column_sections = [index + 1 for index, count in enumerate(column_counts) if count > 1]

    long_paragraphs = [
        (index + 1, len(_words(paragraph.text)), paragraph.text.strip())
        for index, paragraph in enumerate(paragraphs)
        if len(_words(paragraph.text)) > 40
    ]
    long_preview = "; ".join(
        f'paragraph {index} ({count} words: "{text[:55]}…")'
        for index, count, text in long_paragraphs[:4]
    )

    body_drawing_count = len(document.element.body.xpath(".//w:drawing | .//w:pict"))
    header_footer_drawing_count = sum(
        len(part.element.xpath(".//w:drawing | .//w:pict"))
        for part in _header_footer_parts(document)
    )
    image_count = body_drawing_count + header_footer_drawing_count

    body_table_count = len(document.element.body.xpath(".//w:tbl"))
    header_footer_table_count = sum(
        len(part.element.xpath(".//w:tbl"))
        for part in _header_footer_parts(document)
    )
    table_count = body_table_count + header_footer_table_count

    text_paragraphs = [paragraph for paragraph in paragraphs if len(_words(paragraph.text)) >= 5]
    left_aligned = [
        paragraph
        for paragraph in text_paragraphs
        if _paragraph_alignment(paragraph) == WD_ALIGN_PARAGRAPH.LEFT
    ]
    left_ratio = len(left_aligned) / len(text_paragraphs) if text_paragraphs else 1.0
    if left_ratio >= 0.8:
        alignment_status: ReportStatus = "pass"
    elif left_ratio >= 0.6:
        alignment_status = "warning"
    else:
        alignment_status = "fail"

    layout_checks = [
        ReportCheck(
            "Your resume does not contain columns",
            "pass" if not multi_column_sections else "fail",
            "The resume uses a single-column layout, which is easier for ATS to parse."
            if not multi_column_sections
            else "Multiple columns were detected in section(s) "
            + ", ".join(map(str, multi_column_sections))
            + ". Some ATS have trouble accurately parsing columns.",
        ),
        ReportCheck(
            "Your paragraphs are not longer than 40 words",
            "pass" if not long_paragraphs else "warning",
            "Every paragraph contains 40 words or fewer."
            if not long_paragraphs
            else f"{len(long_paragraphs)} paragraph(s) exceed 40 words. Consider shortening them for readability: {long_preview}",
        ),
        ReportCheck(
            "Your resume does not contain images",
            "pass" if image_count == 0 else "fail",
            "No images or drawing objects were detected."
            if image_count == 0
            else f"The resume contains {image_count} image or drawing object(s), which can be ignored or misread by an ATS.",
        ),
        ReportCheck(
            "Your resume does not contain any tables",
            "pass" if table_count == 0 else "fail",
            "No tables were detected in the resume."
            if table_count == 0
            else f"The resume contains {table_count} table(s). Some ATS parse table content out of order or omit it.",
        ),
        ReportCheck(
            "Your resume primarily uses standardized left alignment for text sections",
            alignment_status,
            f"{len(left_aligned)} of {len(text_paragraphs)} text paragraph(s) ({left_ratio:.0%}) are left aligned. "
            "Left alignment is the most predictable option for ATS parsing and recruiter scanning.",
        ),
    ]

    unusual_characters = [
        character
        for character in full_text
        if not character.isalnum()
        and not character.isspace()
        and character not in _COMMON_PUNCTUATION
        and not unicodedata.category(character).startswith("M")
    ]
    unusual_counts = Counter(unusual_characters)
    unusual_limit = max(5, round(max(1, len(_words(full_text))) * 0.01))
    unusual_overused = len(unusual_characters) > unusual_limit

    total_characters = 0
    bold_characters = 0
    font_character_counts: Counter[str] = Counter()
    size_weighted_total = 0.0
    size_character_total = 0
    color_character_counts: Counter[str] = Counter()
    for paragraph in paragraphs:
        for run in paragraph.runs:
            text = run.text.strip()
            if not text:
                continue
            character_count = len(text)
            total_characters += character_count
            if _resolved_bold(paragraph, run):
                bold_characters += character_count
            font_name = _resolved_font_name(document, paragraph, run)
            font_character_counts[font_name] += character_count
            font_size = _resolved_font_size(document, paragraph, run)
            if font_size is not None:
                size_weighted_total += font_size * character_count
                size_character_total += character_count
            color_character_counts[_resolved_rgb(paragraph, run)] += character_count

    bold_ratio = bold_characters / total_characters if total_characters else 0.0
    if bold_ratio <= 0.2:
        bold_status: ReportStatus = "pass"
    elif bold_ratio <= 0.35:
        bold_status = "warning"
    else:
        bold_status = "fail"

    fonts = sorted(font_character_counts)
    font_count = len(fonts)
    if font_count <= 2:
        font_variety_status: ReportStatus = "pass"
    elif font_count == 3:
        font_variety_status = "warning"
    else:
        font_variety_status = "fail"
    nonstandard_fonts = sorted(font for font in fonts if font.casefold() not in _STANDARD_FONTS and font != "Unknown")

    low_contrast_colors = sorted(
        color for color in color_character_counts if re.fullmatch(r"[0-9A-F]{6}", color) and _contrast_with_white(color) < 4.5
    )
    unknown_colors = sorted(color for color in color_character_counts if not re.fullmatch(r"[0-9A-F]{6}", color))
    if low_contrast_colors:
        color_status: ReportStatus = "fail"
    elif unknown_colors:
        color_status = "warning"
    else:
        color_status = "pass"

    average_font_size = size_weighted_total / size_character_total if size_character_total else 0.0
    if 10.0 <= average_font_size <= 12.0:
        size_status: ReportStatus = "pass"
    elif 9.0 <= average_font_size <= 14.0:
        size_status = "warning"
    else:
        size_status = "fail"

    font_checks = [
        ReportCheck(
            "Special characters were not overused in your resume",
            "pass" if not unusual_overused else "warning",
            "No excessive decorative or unusual characters were detected."
            if not unusual_overused
            else f"{len(unusual_characters)} unusual character(s) were detected, above the suggested limit of {unusual_limit}: "
            + ", ".join(f"{character!r} × {count}" for character, count in unusual_counts.most_common(8)),
        ),
        ReportCheck(
            "Your resume does not contain too much bold styling",
            bold_status,
            f"Approximately {bold_ratio:.0%} of visible text is bold. Reserve bold styling mainly for your name, job titles, company names, and section headings.",
        ),
        ReportCheck(
            "All parts of the resume use an easy-to-read font color",
            color_status,
            "All detected text colors have sufficient contrast against a white page; black remains the safest ATS-compatible choice."
            if color_status == "pass"
            else "Low-contrast colors were detected: " + ", ".join(low_contrast_colors or unknown_colors) + ". Use black or another very dark color.",
        ),
        ReportCheck(
            "Your resume does not overuse different fonts",
            font_variety_status,
            f"The resume uses {font_count} detected font(s): {', '.join(fonts) if fonts else 'none detected'}. Keep the document to one or two fonts.",
        ),
        ReportCheck(
            "Your resume does not contain non-standard fonts",
            "pass" if not nonstandard_fonts else "fail",
            "All detected fonts are common, readable, and ATS-compatible."
            if not nonstandard_fonts
            else "Replace these non-standard fonts with Arial, Calibri, Times New Roman, Helvetica, or another common resume font: "
            + ", ".join(nonstandard_fonts),
        ),
        ReportCheck(
            "The average font size meets readability and ATS standards",
            size_status,
            f"The character-weighted average font size is approximately {average_font_size:.1f} pt. A 10-12 pt body-text range is generally readable and ATS-friendly.",
        ),
    ]

    header_parts = [part for part in _header_footer_parts(document) if isinstance(part, HeaderPart)]
    footer_parts = [part for part in _header_footer_parts(document) if isinstance(part, FooterPart)]
    headers_with_information = [part for part in header_parts if _part_text(part)]
    footers_with_information = [part for part in footer_parts if _part_text(part)]

    margin_rows: list[tuple[float, float, float, float]] = []
    for section in document.sections:
        values = (section.top_margin, section.bottom_margin, section.left_margin, section.right_margin)
        if all(value is not None for value in values):
            margin_rows.append(tuple(value.inches for value in values))
    margins_standard = bool(margin_rows) and all(0.5 <= value <= 1.25 for row in margin_rows for value in row)
    margins_consistent = bool(margin_rows) and all(
        max(row[index] for row in margin_rows) - min(row[index] for row in margin_rows) <= 0.05
        for index in range(4)
    )
    if margins_standard and margins_consistent:
        margin_status: ReportStatus = "pass"
    elif margins_standard or margins_consistent:
        margin_status = "warning"
    else:
        margin_status = "fail"
    margin_detail = "; ".join(
        f"section {index + 1}: top {row[0]:.2f}, bottom {row[1]:.2f}, left {row[2]:.2f}, right {row[3]:.2f} inches"
        for index, row in enumerate(margin_rows)
    )

    page_sizes: list[tuple[float, float]] = []
    for section in document.sections:
        if section.page_width is not None and section.page_height is not None:
            page_sizes.append((section.page_width.inches, section.page_height.inches))

    def standard_page_size(width: float, height: float) -> bool:
        ordered = sorted((width, height))
        letter = sorted((8.5, 11.0))
        a4 = sorted((8.27, 11.69))
        return all(abs(actual - expected) <= 0.12 for actual, expected in zip(ordered, letter)) or all(
            abs(actual - expected) <= 0.12 for actual, expected in zip(ordered, a4)
        )

    page_sizes_standard = bool(page_sizes) and all(standard_page_size(width, height) for width, height in page_sizes)
    page_size_detail = "; ".join(
        f"section {index + 1}: {width:.2f} × {height:.2f} inches"
        for index, (width, height) in enumerate(page_sizes)
    )
    page_count, page_count_method = _rendered_page_count(document, exact=exact_page_count)
    page_count_status: ReportStatus = "pass" if page_count <= page_limit else "fail"

    page_setup_checks = [
        ReportCheck(
            "Your resume does not contain information in footers",
            "pass" if not footers_with_information else "fail",
            "No footer text was detected."
            if not footers_with_information
            else f"Information was detected in {len(footers_with_information)} footer part(s). Move essential resume content into the document body.",
        ),
        ReportCheck(
            "Your resume does not contain information in headers",
            "pass" if not headers_with_information else "fail",
            "No header text was detected."
            if not headers_with_information
            else f"Information was detected in {len(headers_with_information)} header part(s). Some ATS omit header content.",
        ),
        ReportCheck(
            "Your margin sizes are consistent and use standard dimensions",
            margin_status,
            (margin_detail or "Margin values could not be resolved.")
            + " Standard resume margins are generally between 0.5 and 1.25 inches and should remain consistent across sections.",
        ),
        ReportCheck(
            "Your document page size is standard",
            "pass" if page_sizes_standard else "fail",
            (page_size_detail or "Page dimensions could not be resolved.")
            + (" Letter or A4 page sizing was detected." if page_sizes_standard else " Use US Letter or A4 page sizing."),
        ),
        ReportCheck(
            f"The resume fits within the {page_limit}-page limit",
            page_count_status,
            f"The resume is {page_count} page(s) using a {page_count_method} page count. "
            + (
                f"It stays within the configured {page_limit}-page maximum."
                if page_count <= page_limit
                else f"It exceeds the configured {page_limit}-page maximum by {page_count - page_limit} page(s). Prune lower-priority content or tighten spacing without reducing readability."
            )
            + (" Install or enable a compatible document renderer for an exact count." if page_count_method == "estimated" else ""),
        ),
    ]

    if inspection_note:
        layout_checks.insert(
            0,
            ReportCheck("Formatting inspection note", "info", inspection_note),
        )

    return [
        ReportSubsection("Layout", layout_checks),
        ReportSubsection("Font Check", font_checks),
        ReportSubsection("Typography Consistency", _typography_checks(document)),
        ReportSubsection("Page Setup", page_setup_checks),
    ]


def build_resume_report(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    *,
    generated_filename: str,
    template_path: str | Path | None = None,
    job_description: str = "",
    resume_title: str | None = None,
    candidate_answers: list[CandidateAnswer] | None = None,
    page_limit: int = 2,
    generated_document_bytes: bytes | None = None,
    exact_page_count: bool = False,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
) -> ResumeReport:
    effective_resume_title = (resume_title or analysis.target_title).strip()
    selected_bullets = _selected_bullets(proposal)
    selected_ids_by_experience = _selected_bullet_ids_by_experience(profile, proposal)
    page_limit = max(1, int(page_limit))
    report_document, formatting_note = _document_for_report(
        template_path,
        profile,
        proposal,
        effective_resume_title,
        generated_document_bytes,
        career_stage=career_stage,
        resume_format=resume_format,
        visual_design=visual_design,
    )

    contact_checks = [
        ReportCheck(
            "You provided your full name",
            "pass" if len(_words(profile.name)) >= 2 else "warning" if profile.name.strip() else "fail",
            "A complete candidate name is available for the resume header."
            if len(_words(profile.name)) >= 2
            else "Provide the candidate's full professional name for the resume header.",
        ),
        ReportCheck(
            "You provided your physical address or location",
            "pass" if profile.contact.location.strip() else "fail",
            "Recruiters use your address or location to validate your location for job matches."
            if profile.contact.location.strip()
            else "Add a city and state, or another appropriate location, so recruiters can validate location-based matches.",
        ),
        ReportCheck(
            "You provided your email",
            "pass" if _EMAIL_PATTERN.match(profile.contact.email.strip()) else "fail",
            "Recruiters can use the email shown on the resume to contact you."
            if _EMAIL_PATTERN.match(profile.contact.email.strip())
            else "Add a valid professional email address.",
        ),
        ReportCheck(
            "You provided your phone number",
            "pass" if len(re.sub(r"\D", "", profile.contact.phone)) >= 10 else "fail",
            "A recruiter-ready phone number is present."
            if len(re.sub(r"\D", "", profile.contact.phone)) >= 10
            else "Add a complete phone number, including area code.",
        ),
    ]

    summary_checks = [
        ReportCheck(
            "We found a summary section on your resume",
            "pass" if proposal.professional_summary.strip() else "fail",
            "The summary provides a quick overview of the candidate's qualifications and value."
            if proposal.professional_summary.strip()
            else "Add a concise professional summary tailored to the target role.",
        )
    ]

    work_history_complete = bool(
        profile.experiences
        and selected_bullets
        and all(experience.employer and experience.title and experience.dates for experience in profile.experiences)
    )
    education_complete = bool(
        profile.education
        and all(item.credential.strip() and item.institution.strip() and item.date.strip() for item in profile.education)
    )
    heading_checks = [
        ReportCheck(
            'We found an "Education" section in your resume',
            "pass" if education_complete else "fail",
            'The resume includes complete education entries under an ATS-recognizable Education heading.'
            if education_complete
            else 'Add an Education section and ensure every entry includes a credential, institution, and date.',
        ),
        ReportCheck(
            "We found the work experience section in your resume",
            "pass" if profile.experiences else "fail",
            "A work experience section is present."
            if profile.experiences
            else "Add a Work Experience or Professional Experience section.",
        ),
        ReportCheck(
            "We found work history in your resume",
            "pass" if work_history_complete else "fail",
            "Employer names, job titles, dates, and selected accomplishments are present."
            if work_history_complete
            else "Include employer names, titles, dates, and at least one accomplishment bullet.",
        ),
    ]

    target_title = analysis.target_title.strip()
    normalized_target_title = _normalize(target_title)
    normalized_resume_title = _normalize(effective_resume_title)
    if target_title and effective_resume_title and normalized_target_title == normalized_resume_title:
        title_status: ReportStatus = "pass"
        title_detail = (
            f'The resume profile title "{effective_resume_title}" exactly matches the analyzed job title.'
        )
    elif target_title and effective_resume_title and (
        normalized_target_title in normalized_resume_title
        or normalized_resume_title in normalized_target_title
    ):
        title_status = "warning"
        title_detail = (
            f'The resume profile title "{effective_resume_title}" is related to the target title '
            f'"{target_title}", but it is not an exact match. Use the exact target title only when it accurately describes the candidate.'
        )
    else:
        title_status = "fail"
        title_detail = (
            f'The resume profile title "{effective_resume_title or "not provided"}" does not match the target title '
            f'"{target_title or "not identified"}". Recruiter searches commonly use exact job titles.'
        )
    title_checks = [
        ReportCheck(
            "The job title matches the resume profile title",
            title_status,
            title_detail,
        )
    ]

    invalid_dates = [
        experience.dates
        for experience in profile.experiences
        if not _DATE_RANGE_PATTERN.match(experience.dates.strip())
    ]
    date_checks = [
        ReportCheck(
            "Work-experience dates are properly formatted",
            "pass" if not invalid_dates else "fail",
            "All work dates use a consistent MM/YYYY - MM/YYYY format."
            if not invalid_dates
            else "Reformat these date ranges consistently: " + "; ".join(invalid_dates),
        )
    ]

    filename_without_extension = Path(generated_filename).stem
    filename_has_specials = bool(re.search(r"[^A-Za-z0-9 _.-]", generated_filename))
    readable_filename = 8 <= len(filename_without_extension) <= 80 and not re.fullmatch(r"[A-Za-z0-9]{20,}", filename_without_extension)
    file_checks = [
        ReportCheck(
            "You are using a .docx resume",
            "warning" if generated_filename.casefold().endswith(".docx") else "fail",
            "The application generates a .docx resume. Most ATS can process .docx files, but a PDF copy can preserve appearance more consistently; use the format requested by the employer.",
        ),
        ReportCheck(
            "The file name does not contain problematic special characters",
            "pass" if not filename_has_specials else "fail",
            "The proposed file name uses ATS-safe characters."
            if not filename_has_specials
            else "Remove special characters that could cause an upload or ATS parsing error.",
        ),
        ReportCheck(
            "The file name is concise and readable",
            "pass" if readable_filename else "warning",
            f'The proposed file name is "{generated_filename}".'
            if readable_filename
            else "Use a clear name such as Firstname_Lastname_TargetRole_Resume.docx.",
        ),
    ]

    searchability = ReportSection(
        "Searchability",
        "An ATS (Applicant Tracking System) is a software used by 90% of companies and recruiters to search for resumes and manage the hiring process. Below is how well your resume appears in an ATS and a recruiter search. Tip: Fix the red Xs to ensure your resume is easily searchable by recruiters and parsed correctly by the ATS.",
        [
            ReportSubsection("Contact Information", contact_checks),
            ReportSubsection("Summary", summary_checks),
            ReportSubsection("Section Headings", heading_checks),
            ReportSubsection("Job Title Match", title_checks),
            ReportSubsection("Date Formatting", date_checks),
            ReportSubsection("Education Match", [_education_match(profile, analysis)]),
            ReportSubsection("File Type", file_checks),
        ],
    )

    hard_skills = ReportSection(
        "Hard skills",
        "Hard skills enable you to perform job-specific duties and responsibilities. You can learn hard skills in the classroom, training courses, and on the job. These skills are typically focused on teachable tasks and measurable abilities, such as the use of tools, equipment, or software. Hard skills have a high impact on your match score. Tip: Match the skills in your resume to the exact spelling in the job description. Prioritize skills that appear most frequently in the job description, while adding only skills supported by verified experience.",
        [_hard_skill_comparison_subsection(profile, analysis, proposal, job_description, effective_resume_title)],
    )

    soft_skills = ReportSection(
        "Soft skills",
        "Soft skills are your traits and abilities that are not unique to any one job. They are part of your professional behavior and can also be learned. These skills typically help you succeed at any company, such as time management and communication. Soft skills have a medium impact on your match score. Tip: Prioritize hard skills in your resume to get interviews, and then showcase your soft skills in the interview to get jobs. The comparison below uses the exact spelling found in the job description and counts each occurrence in the current proposed resume.",
        [_soft_skill_comparison_subsection(profile, analysis, proposal, job_description, effective_resume_title)],
    )
    content_quality = _content_quality_section(
        profile,
        analysis,
        proposal,
        selected_bullets,
        report_document,
        formatting_note,
        candidate_answers,
        career_stage=career_stage,
        resume_format=resume_format,
    )

    summary_word_count = len(_words(proposal.professional_summary))
    measurable_mentions = _NUMBER_PATTERN.findall(" ".join(selected_bullets))
    measurable_count = len(measurable_mentions)
    action_count = 0
    opening_words: list[str] = []
    for bullet in selected_bullets:
        words = _words(bullet)
        if words:
            opener = words[0].casefold()
            opening_words.append(opener)
            if opener in _ACTION_VERBS:
                action_count += 1
    action_ratio = action_count / len(selected_bullets) if selected_bullets else 0.0
    repeated_openers = sorted({word for word in opening_words if opening_words.count(word) > 2})
    unsupported_critical = [
        requirement.requirement
        for requirement in analysis.requirements
        if requirement.priority == "critical"
        and any(
            match.requirement_id == requirement.id and match.status == "unsupported"
            for match in proposal.evidence_matches
        )
    ]

    bullet_count_warnings = []
    for experience in profile.experiences:
        count = len(selected_ids_by_experience.get(experience.id, []))
        if count < 2 or count > 7:
            bullet_count_warnings.append(f"{experience.employer}: {count}")

    resume_text = " ".join([proposal.professional_summary, *selected_bullets])
    normalized_resume_text = _normalize(resume_text)
    found_cliches = sorted(
        phrase for phrase in _CLICHES_AND_BUZZWORDS if _normalize(phrase) in normalized_resume_text
    )
    found_negative_phrases = sorted(
        phrase for phrase in _NEGATIVE_RESUME_PHRASES if _normalize(phrase) in normalized_resume_text
    )
    if found_negative_phrases:
        tone_status: ReportStatus = "fail"
        tone_detail = "Potentially negative wording was found: " + ", ".join(found_negative_phrases) + ". Reframe it around actions, learning, and positive outcomes."
    elif found_cliches:
        tone_status = "warning"
        tone_detail = "The overall tone is positive, but these common clichés or buzzwords were found: " + ", ".join(found_cliches) + ". Replace them with specific evidence."
    elif action_ratio < 0.6:
        tone_status = "warning"
        tone_detail = f"No common clichés were found, but only {action_count} of {len(selected_bullets)} selected bullets begin with a recognized action verb. Use more direct, positive accomplishment language."
    else:
        tone_status = "pass"
        tone_detail = "The resume uses generally positive, evidence-based language, and no common clichés or buzzwords were found."

    professional_domains, web_error = _professional_web_links(template_path, profile)
    if web_error:
        web_status: ReportStatus = "warning"
        web_detail = web_error
    elif professional_domains:
        web_status = "pass"
        web_detail = "Professional web links were found for: " + ", ".join(professional_domains) + ". Recruiters appreciate the convenience and credibility of a professional website or profile."
    else:
        web_status = "warning"
        web_detail = "Add a working LinkedIn, GitHub, portfolio, or professional website link to build web credibility and make verification easier for recruiters."

    total_resume_words = _estimated_resume_word_count(profile, analysis, proposal, effective_resume_title)

    job_level_checks = [
        _job_level_match(profile, analysis),
        ReportCheck(
            "Critical gaps are disclosed instead of invented",
            "pass" if not unsupported_critical else "warning",
            "No unsupported critical requirement was converted into a resume claim."
            if not unsupported_critical
            else "The following critical requirements remain gaps: " + "; ".join(unsupported_critical),
        ),
    ]
    measurable_checks = [
        ReportCheck(
            "There are five or more mentions of measurable results",
            "pass" if measurable_count >= 5 else "warning" if measurable_count >= 3 else "fail",
            f"The selected accomplishment bullets contain {measurable_count} measurable mention(s). Employers like to see the impact, scale, and results you delivered on the job.",
        )
    ]
    tone_checks = [
        ReportCheck(
            "The resume tone is positive and avoids common clichés and buzzwords",
            tone_status,
            tone_detail,
        ),
        ReportCheck(
            "Bullet openings are varied",
            "pass" if not repeated_openers else "warning",
            "Selected bullets use varied opening verbs."
            if not repeated_openers
            else "These opening words are repeated more than twice: " + ", ".join(repeated_openers),
        ),
    ]
    web_presence_checks = [
        ReportCheck(
            "You linked to a website that builds your web credibility",
            web_status,
            web_detail,
        )
    ]
    word_count_checks = [
        ReportCheck(
            "The resume contains fewer than 1,000 words",
            "pass" if total_resume_words < 1000 else "fail",
            f"The proposed complete resume contains approximately {total_resume_words} words. Keeping it under 1,000 words improves relevance and ease of reading.",
        ),
        ReportCheck(
            "The professional summary is concise",
            "pass" if 50 <= summary_word_count <= 80 else "warning",
            f"The proposed summary contains {summary_word_count} words; 50-80 words is a useful target for this template.",
        ),
        ReportCheck(
            "The number of bullets is recruiter-friendly",
            "pass" if not bullet_count_warnings else "warning",
            "Each role has between 2 and 7 selected bullets."
            if not bullet_count_warnings
            else "Review bullet counts for " + "; ".join(bullet_count_warnings) + ".",
        ),
    ]
    recruiter_tips = ReportSection(
        "Recruiter tips",
        "These checks review job-level fit, quantified impact, professional tone, web credibility, and overall resume length from a recruiter's perspective.",
        [
            ReportSubsection("Job Level Match", job_level_checks),
            ReportSubsection("Measurable Results", measurable_checks),
            ReportSubsection("Resume Tone", tone_checks),
            ReportSubsection("Web Presence", web_presence_checks),
            ReportSubsection("Word Count", word_count_checks),
        ],
    )

    formatting = ReportSection(
        "Formatting",
        "These checks inspect the proposed Word resume for ATS-friendly layout, readable and consistent typography, and standard page setup.",
        _formatting_sections(
            report_document,
            formatting_note,
            page_limit,
            exact_page_count=exact_page_count,
        ),
    )
    evidence_gaps = _evidence_gaps_section(
        profile,
        analysis,
        proposal,
        candidate_answers,
    )

    return ResumeReport(
        searchability=searchability,
        hard_skills=hard_skills,
        soft_skills=soft_skills,
        content_quality=content_quality,
        recruiter_tips=recruiter_tips,
        formatting=formatting,
        evidence_gaps=evidence_gaps,
    )
