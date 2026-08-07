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

from .bullet_text import (
    bullet_has_multiple_complete_sentences,
    normalize_resume_bullet_terminal_punctuation,
)
from .docx_export import export_resume_docx
from .docx_styles import normalize_career_stage, normalize_resume_format
from .resume_pagination import estimate_resume_pagination
from .validation import adjacent_repeated_words, validate_proposal
from .models import (
    ApprovedResume,
    BulletProposal,
    CandidateAnswer,
    CareerTranslationAssessment,
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
        career_translation_assessment=(
            evidence_source.career_translation_assessment.model_copy(deep=True)
            if evidence_source
            else CareerTranslationAssessment()
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


_SKILL_PRIORITY_WEIGHTS = {
    "critical": 1.5,
    "important": 1.2,
    "secondary": 1.0,
}

_EVIDENCE_PRIORITY_WEIGHTS = {
    "critical": 3.0,
    "important": 2.0,
    "secondary": 1.0,
}

from . import resume_report_matching
from . import resume_report_evidence
from . import resume_report_content
from . import resume_report_document
from . import resume_report_formatting
from . import resume_report_builder

_REPORT_MODULES = (
    resume_report_matching,
    resume_report_evidence,
    resume_report_content,
    resume_report_document,
    resume_report_formatting,
    resume_report_builder,
)

_REPORT_EXPORTS: dict[str, object] = {}
_REPORT_NAMESPACE = globals()
for _module in _REPORT_MODULES:
    _exported = _module.exports()
    _REPORT_EXPORTS.update(_exported)
    _REPORT_NAMESPACE.update(_exported)
for _module in _REPORT_MODULES:
    _module.activate(_REPORT_NAMESPACE)
globals().update(_REPORT_EXPORTS)
