from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol

from career_bridge.domain.enums import EvidenceVerificationStatus
from career_bridge.domain.fit_scoring import (
    ApplicationFitAssessment,
    RequirementCategory,
    RequirementPriority,
    RequirementStatus,
    ScorableRequirement,
    build_requirement_fit_assessment,
)
from career_bridge.domain.models import (
    CandidateProfile as CareerCandidateProfile,
    CareerBackground,
    EvidenceItem,
)
from products.resume_taylor.resume_tailor.models import (
    CandidateProfile as ResumeCandidateProfile,
    JobAnalysis,
    NewcomerCareerProfile,
)

from .models import (
    DiscoveredJob,
    EvidenceReference,
    JobFitSnapshot,
    RequirementEvidenceMatch,
    WorkplaceType,
    profile_fingerprint,
)
from .normalization import stable_text_key


_GENERIC_REQUIREMENT_TOKENS = {
    "ability",
    "experience",
    "knowledge",
    "proficiency",
    "required",
    "requirement",
    "skill",
    "skills",
    "years",
}
_NO_SPONSORSHIP_PATTERNS = (
    r"\bno (?:visa )?sponsorship\b",
    r"\b(?:visa )?sponsorship (?:is )?not (?:available|provided)\b",
    r"\bunable to sponsor\b",
)
_WORK_AUTHORIZATION_PATTERNS = (
    r"\bmust be (?:legally )?authorized to work\b",
    r"\bwork authorization (?:is )?required\b",
)
_US_CITIZEN_PATTERNS = (
    r"\bmust be (?:a )?(?:u\.s\.?|us|united states) citizen\b",
    r"\b(?:u\.s\.?|us|united states) citizenship (?:is )?required\b",
)
_CLEARANCE_PATTERNS = (
    r"\b(?:active )?(?:security )?clearance (?:is )?required\b",
    r"\bmust (?:hold|maintain|possess) (?:an? )?(?:active )?(?:security )?clearance\b",
)
_LICENSE_PATTERNS = (
    r"\b(?:professional |state )?licen[cs]e (?:is )?required\b",
    r"\brequired (?:professional |state )?licen[cs]e\b",
)
_PREFERENCE_ONLY_REQUIREMENT_PATTERNS = (
    r"\b(?:salary|compensation|pay range|base pay|hourly rate)\b",
    r"(?:[$£€]\s?\d|\b\d{2,3}[,\d]*\s*(?:usd|cad|eur|gbp|per year|annually|hourly)\b)",
    r"\b(?:remote|hybrid|on[- ]?site) (?:role|position|work|schedule|arrangement)\b",
    r"\b(?:work )?location\b",
    r"\b(?:based in|must be located|within commuting distance)\b",
    r"\bmust reside\b",
    r"\blocal candidates only\b",
    r"\brelocation (?:is )?(?:required|available|provided)\b",
    r"\b(?:full[- ]time|part[- ]time|contract|temporary) (?:role|position|employment)\b",
    r"\b(?:job|position) title\b",
)

SEARCH_PRIORITY_FIT_WEIGHT = 0.70
SEARCH_PRIORITY_PREFERENCE_WEIGHT = 0.20
SEARCH_PRIORITY_FRESHNESS_WEIGHT = 0.10
SEARCH_PRIORITY_FORMULA = (
    "70% Job Fit + 20% Preference Fit + 10% Posting Freshness"
)


@dataclass(frozen=True, slots=True)
class CandidateJobProfile:
    """Verified evidence, search preferences, and explicit eligibility facts."""

    target_titles: tuple[str, ...] = ()
    verified_skills: tuple[str, ...] = ()
    evidence_statements: tuple[str, ...] = ()
    evidence_references: tuple[EvidenceReference, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    accepts_remote: bool = True
    preferred_employment_types: tuple[str, ...] = ()
    preferred_keywords: tuple[str, ...] = ()
    required_keywords: tuple[str, ...] = ()

    # Stage-one mandatory preferences. Defaults preserve broad discovery.
    accepted_workplace_types: tuple[WorkplaceType | str, ...] = ()
    minimum_salary: float | None = None
    minimum_salary_currency: str = ""
    minimum_salary_interval: str = "year"
    excluded_terms: tuple[str, ...] = ()
    excluded_title_terms: tuple[str, ...] = ()
    require_title_match: bool = False
    require_location_match: bool = False
    require_workplace_match: bool = False
    require_employment_type_match: bool = False

    # Explicit facts used only for obvious deterministic blockers.
    requires_sponsorship: bool | None = None
    work_authorized: bool | None = None
    us_citizen: bool | None = None
    security_clearances: tuple[str, ...] = ()
    licenses_certifications: tuple[str, ...] = ()
    eligibility_profile_complete: bool = False

    def __post_init__(self) -> None:
        for name in (
            "target_titles",
            "verified_skills",
            "evidence_statements",
            "preferred_locations",
            "preferred_employment_types",
            "preferred_keywords",
            "required_keywords",
            "excluded_terms",
            "excluded_title_terms",
            "security_clearances",
            "licenses_certifications",
        ):
            object.__setattr__(self, name, _clean_values(getattr(self, name)))
        normalized_references: list[EvidenceReference] = []
        seen_references: set[tuple[str, str, str]] = set()
        for raw in self.evidence_references or ():
            reference = (
                raw
                if isinstance(raw, EvidenceReference)
                else EvidenceReference(**dict(raw))
            )
            key = (reference.record_type, reference.record_id, reference.field_name)
            if key in seen_references:
                continue
            seen_references.add(key)
            normalized_references.append(reference)
        object.__setattr__(self, "evidence_references", tuple(normalized_references))

        normalized_workplaces: list[WorkplaceType] = []
        for value in self.accepted_workplace_types or ():
            normalized = value if isinstance(value, WorkplaceType) else WorkplaceType(str(value))
            if normalized not in normalized_workplaces:
                normalized_workplaces.append(normalized)
        object.__setattr__(self, "accepted_workplace_types", tuple(normalized_workplaces))
        if self.minimum_salary is not None and float(self.minimum_salary) < 0:
            raise ValueError("minimum_salary cannot be negative")
        if self.minimum_salary is not None:
            object.__setattr__(self, "minimum_salary", float(self.minimum_salary))
        object.__setattr__(
            self,
            "minimum_salary_currency",
            str(self.minimum_salary_currency or "").strip().upper(),
        )
        object.__setattr__(
            self,
            "minimum_salary_interval",
            stable_text_key(self.minimum_salary_interval or "year") or "year",
        )

    @property
    def fingerprint(self) -> str:
        """Fingerprint only the verified evidence used by Job Fit.

        Search preferences intentionally do not invalidate an evidence-fit
        snapshot. Changing a location, salary, workplace, or employment-type
        preference should recompute Search Priority without pretending the
        candidate's professional evidence changed.
        """

        return self.evidence_fingerprint

    @property
    def evidence_fingerprint(self) -> str:
        return profile_fingerprint(
            {
                "verified_skills": self.verified_skills,
                "evidence_statements": self.evidence_statements,
                "evidence_references": tuple(
                    (
                        item.record_type,
                        item.record_id,
                        item.field_name,
                        item.statement,
                        item.verification_status,
                    )
                    for item in self.evidence_references
                ),
                "security_clearances": self.security_clearances,
                "licenses_certifications": self.licenses_certifications,
            }
        )

    @property
    def preference_fingerprint(self) -> str:
        return profile_fingerprint(
            {
                "target_titles": self.target_titles,
                "preferred_locations": self.preferred_locations,
                "accepts_remote": self.accepts_remote,
                "preferred_employment_types": self.preferred_employment_types,
                "preferred_keywords": self.preferred_keywords,
                "required_keywords": self.required_keywords,
                "accepted_workplace_types": tuple(
                    value.value for value in self.accepted_workplace_types
                ),
                "minimum_salary": self.minimum_salary,
                "minimum_salary_currency": self.minimum_salary_currency,
                "minimum_salary_interval": self.minimum_salary_interval,
                "excluded_terms": self.excluded_terms,
                "excluded_title_terms": self.excluded_title_terms,
                "require_title_match": self.require_title_match,
                "require_location_match": self.require_location_match,
                "require_workplace_match": self.require_workplace_match,
                "require_employment_type_match": self.require_employment_type_match,
                "requires_sponsorship": self.requires_sponsorship,
                "work_authorized": self.work_authorized,
                "us_citizen": self.us_citizen,
                "eligibility_profile_complete": self.eligibility_profile_complete,
            }
        )

    @classmethod
    def from_career_records(
        cls,
        profile: CareerCandidateProfile,
        background: CareerBackground,
        evidence_items: Iterable[EvidenceItem] = (),
        *,
        preferred_locations: tuple[str, ...] | None = None,
        accepts_remote: bool = True,
        preferred_employment_types: tuple[str, ...] = (),
        **preference_overrides: Any,
    ) -> "CandidateJobProfile":
        """Build discovery inputs without treating profile text as evidence.

        Career Profile and Career Background values may guide stage-one search
        preferences. Only candidate-confirmed or document-verified Career
        Evidence Library items may support evidence-grounded Job Fit.
        """

        if background.candidate_profile_id != profile.id:
            raise ValueError("career background does not belong to candidate profile")

        verified_statuses = {
            EvidenceVerificationStatus.CANDIDATE_CONFIRMED,
            EvidenceVerificationStatus.DOCUMENT_VERIFIED,
        }
        verified_items = tuple(
            item
            for item in evidence_items
            if item.candidate_profile_id == profile.id
            and item.verification_status in verified_statuses
        )
        references = tuple(
            EvidenceReference(
                record_id=item.id,
                record_type="evidence_item",
                field_name="statement",
                label="Career Evidence Library · Verified evidence",
                statement=item.statement,
                verification_status=item.verification_status.value,
            )
            for item in verified_items
        )

        overrides = dict(preference_overrides)
        # Self-entered skills and certifications are useful discovery keywords,
        # but they remain preference context and cannot improve Job Fit.
        overrides.setdefault(
            "preferred_keywords",
            tuple(dict.fromkeys((*background.skills, *background.certification_names))),
        )

        return cls(
            target_titles=profile.preferred_roles,
            verified_skills=(),
            evidence_statements=tuple(item.statement for item in verified_items),
            evidence_references=references,
            preferred_locations=(
                preferred_locations
                if preferred_locations is not None
                else ((profile.location,) if profile.location else ())
            ),
            accepts_remote=accepts_remote,
            preferred_employment_types=preferred_employment_types,
            licenses_certifications=(),
            **overrides,
        )

    @classmethod
    def from_resume_workflow(
        cls,
        profile: ResumeCandidateProfile,
        background: NewcomerCareerProfile | None = None,
        *,
        target_title: str = "",
        preferred_locations: tuple[str, ...] | None = None,
        accepts_remote: bool = True,
        preferred_employment_types: tuple[str, ...] = (),
        **preference_overrides: Any,
    ) -> "CandidateJobProfile":
        """Build discovery inputs from the verified Resume Workflow snapshot.

        The workflow profile is candidate-owned source data. Every statement
        admitted to evidence matching receives a stable source record ID so the
        result card can point back to the exact summary, skill, experience,
        bullet, education entry, or candidate-confirmed evidence item.
        """

        background = background or NewcomerCareerProfile()
        profile_record_id = "resume-profile-" + hashlib.sha256(
            profile.all_source_text().encode("utf-8")
        ).hexdigest()[:20]
        references: list[EvidenceReference] = []
        statements: list[str] = []

        def add_reference(
            *,
            record_id: str,
            record_type: str,
            field_name: str,
            label: str,
            statement: str,
            verification_status: str = "resume_source",
        ) -> None:
            text = " ".join(str(statement or "").split())
            if not text:
                return
            statements.append(text)
            references.append(
                EvidenceReference(
                    record_id=record_id,
                    record_type=record_type,
                    field_name=field_name,
                    label=label,
                    statement=text,
                    verification_status=verification_status,
                )
            )

        add_reference(
            record_id=profile_record_id,
            record_type="candidate_profile",
            field_name="current_summary",
            label="Resume source · Professional summary",
            statement=profile.current_summary,
        )
        for skill in profile.skills.all_non_language_skills():
            add_reference(
                record_id=profile_record_id,
                record_type="candidate_profile",
                field_name=f"skill:{stable_text_key(skill)}",
                label="Resume source · Skill",
                statement=skill,
            )
        for language in profile.skills.languages:
            add_reference(
                record_id=profile_record_id,
                record_type="candidate_profile",
                field_name=f"language:{stable_text_key(language)}",
                label="Resume source · Language",
                statement=language,
            )
        for experience in profile.experiences:
            add_reference(
                record_id=experience.id,
                record_type="career_experience",
                field_name="role",
                label=f"Resume source · {experience.title} at {experience.employer}",
                statement=" ".join(
                    value
                    for value in (experience.title, experience.employer, experience.dates)
                    if value
                ),
            )
            for bullet in experience.bullets:
                add_reference(
                    record_id=bullet.id,
                    record_type="evidence_item",
                    field_name="statement",
                    label=f"Resume source · {experience.title} at {experience.employer}",
                    statement=bullet.text,
                    verification_status="resume_verified",
                )
        for index, education in enumerate(profile.education, start=1):
            add_reference(
                record_id=f"{profile_record_id}-education-{index}",
                record_type="education_record",
                field_name="credential",
                label=f"Resume source · {education.credential}",
                statement=" ".join(
                    value
                    for value in (
                        education.credential,
                        education.institution,
                        education.detail,
                    )
                    if value
                ),
            )
        for evidence in profile.supplemental_evidence:
            add_reference(
                record_id=evidence.id,
                record_type="evidence_item",
                field_name="statement",
                label="Career Evidence Library · Candidate-confirmed evidence",
                statement=evidence.statement,
                verification_status="candidate_confirmed",
            )

        target_titles = tuple(
            dict.fromkeys(
                value
                for value in (
                    target_title,
                    background.target_role,
                    *background.preferred_roles,
                    *background.roles,
                    *background.unfamiliar_job_titles,
                )
                if str(value or "").strip()
            )
        )
        # Reusable Career Profile credentials remain context only. A credential
        # can affect evidence-grounded Job Fit only when it is present in the
        # uploaded resume source or candidate-confirmed Evidence Library.
        certifications: tuple[str, ...] = ()
        locations = (
            preferred_locations
            if preferred_locations is not None
            else (
                (profile.contact.location,)
                if profile.contact.location
                else (
                    (background.current_location,)
                    if background.current_location
                    else ()
                )
            )
        )
        return cls(
            target_titles=target_titles,
            # Profile-entered skills, accomplishments, and credentials may
            # guide search preferences, but cannot improve evidence-grounded
            # Job Fit without resume or evidence-library support.
            verified_skills=tuple(
                dict.fromkeys(profile.all_verified_skills())
            ),
            evidence_statements=tuple(dict.fromkeys(statements)),
            evidence_references=tuple(references),
            preferred_locations=locations,
            accepts_remote=accepts_remote,
            preferred_employment_types=preferred_employment_types,
            licenses_certifications=certifications,
            **preference_overrides,
        )


@dataclass(frozen=True, slots=True)
class DiscoveryRequirement:
    id: str
    category: RequirementCategory
    priority: RequirementPriority
    requirement: str
    keywords: tuple[str, ...] = ()
    origin: str = "posting"


@dataclass(frozen=True, slots=True)
class PreferenceScoreComponent:
    """One visible component of the preference-only score."""

    name: str
    score: float
    weight: float
    explanation: str


@dataclass(frozen=True, slots=True)
class StageOneEvaluation:
    job: DiscoveredJob
    passed: bool
    preference_score: float
    freshness_score: float
    preference_components: tuple[PreferenceScoreComponent, ...] = ()
    freshness_explanation: str = ""
    reasons: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()

    @property
    def score(self) -> float:
        """Compatibility alias for the former ambiguous Stage 1 score."""

        return self.preference_score


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: DiscoveredJob
    fit_score: float
    preference_score: float
    freshness_score: float
    search_priority: float
    priority_formula: str
    reasons: tuple[str, ...]
    fit_snapshot: JobFitSnapshot
    assessment: ApplicationFitAssessment | None
    stage_one: StageOneEvaluation | None = None
    cache_hit: bool = False

    @property
    def score(self) -> float:
        """Compatibility alias for the original evidence-fit score."""

        return self.fit_score


class AnalyzedJob(Protocol):
    requirements: Iterable[ScorableRequirement]


def evaluate_stage_one(
    job: DiscoveredJob,
    profile: CandidateJobProfile,
    *,
    evaluated_at: datetime | str | None = None,
) -> StageOneEvaluation:
    """Cheap deterministic filtering performed before any model call."""

    reasons: list[str] = []
    rejected: list[str] = []
    preference_components: list[PreferenceScoreComponent] = []
    title_searchable = stable_text_key(job.title)
    searchable = stable_text_key(" ".join((job.title, job.company, job.description)))

    excluded_title_matches = [
        term
        for term in profile.excluded_title_terms
        if stable_text_key(term) in title_searchable
    ]
    if excluded_title_matches:
        rejected.append(
            "Excluded job-title term: " + ", ".join(excluded_title_matches)
        )
    excluded_title_keys = {
        stable_text_key(term) for term in excluded_title_matches
    }

    excluded_matches = [
        term
        for term in profile.excluded_terms
        if stable_text_key(term) in searchable
        and stable_text_key(term) not in excluded_title_keys
    ]
    if excluded_matches:
        rejected.append("Excluded term: " + ", ".join(excluded_matches))

    required_keyword_matches = tuple(
        term for term in profile.required_keywords if stable_text_key(term) in searchable
    )
    missing_required_keywords = tuple(
        term for term in profile.required_keywords if term not in required_keyword_matches
    )
    if missing_required_keywords:
        rejected.append(
            "Required keyword not found: " + ", ".join(missing_required_keywords)
        )
    if profile.required_keywords:
        reasons.append(
            f"Required keywords matched {len(required_keyword_matches)}/{len(profile.required_keywords)}"
        )

    preferred_keyword_matches = tuple(
        term for term in profile.preferred_keywords if stable_text_key(term) in searchable
    )
    if profile.preferred_keywords:
        keyword_score = 100.0 * len(preferred_keyword_matches) / len(profile.preferred_keywords)
        preference_components.append(
            PreferenceScoreComponent(
                name="Preferred keywords",
                score=round(keyword_score, 2),
                weight=15.0,
                explanation=(
                    f"Matched {len(preferred_keyword_matches)} of "
                    f"{len(profile.preferred_keywords)} preferred keywords"
                ),
            )
        )
        reasons.append(
            f"Preferred keywords matched {len(preferred_keyword_matches)}/{len(profile.preferred_keywords)}"
        )

    title_score = _best_overlap(job.title, profile.target_titles)
    if profile.target_titles:
        preference_components.append(
            PreferenceScoreComponent(
                name="Desired title",
                score=round(title_score * 100, 2),
                weight=30.0,
                explanation=f"Title overlap {round(title_score * 100)}%",
            )
        )
        reasons.append(f"Title overlap {round(title_score * 100)}%")
        if profile.require_title_match and title_score < 0.4:
            rejected.append("Job title does not match required title terms")

    skill_score = _skill_overlap(job, profile)
    if profile.verified_skills:
        reasons.append(f"Deterministic skill overlap {round(skill_score * 100)}%")

    location_match = _location_match(job, profile)
    if profile.preferred_locations:
        location_value = 100.0 if location_match else (50.0 if not job.location and not job.locations else 0.0)
        preference_components.append(
            PreferenceScoreComponent(
                name="Location",
                score=location_value,
                weight=25.0,
                explanation=(
                    "Preferred location matched"
                    if location_match
                    else "Location was not published"
                    if location_value == 50.0
                    else "Preferred location not matched"
                ),
            )
        )
        reasons.append("Preferred location matched" if location_match else "Preferred location not matched")
        if profile.require_location_match and not location_match:
            rejected.append("Job does not satisfy the required location preference")

    workplace_match = _workplace_match(job, profile)
    if profile.accepted_workplace_types:
        workplace_value = (
            50.0
            if job.workplace_type is WorkplaceType.UNSPECIFIED
            else 100.0 if workplace_match else 0.0
        )
        preference_components.append(
            PreferenceScoreComponent(
                name="Workplace",
                score=workplace_value,
                weight=20.0,
                explanation=(
                    "Workplace type was not published"
                    if job.workplace_type is WorkplaceType.UNSPECIFIED
                    else "Workplace preference matched"
                    if workplace_match
                    else "Workplace preference not matched"
                ),
            )
        )
        reasons.append("Workplace preference matched" if workplace_match else "Workplace preference not matched")
    if job.workplace_type is WorkplaceType.REMOTE and not profile.accepts_remote:
        rejected.append("Remote roles are excluded by the candidate preference")
    elif profile.require_workplace_match and not workplace_match:
        rejected.append("Job does not satisfy the required workplace preference")

    employment_match = _employment_type_match(job, profile)
    if profile.preferred_employment_types:
        employment_value = (
            50.0 if not job.employment_type else 100.0 if employment_match else 0.0
        )
        preference_components.append(
            PreferenceScoreComponent(
                name="Employment type",
                score=employment_value,
                weight=10.0,
                explanation=(
                    "Employment type was not published"
                    if not job.employment_type
                    else "Employment type matched"
                    if employment_match
                    else "Employment type not matched"
                ),
            )
        )
        reasons.append("Employment type matched" if employment_match else "Employment type not matched")
        if profile.require_employment_type_match and not employment_match:
            rejected.append("Job does not satisfy the required employment type")

    salary_match = _salary_match(job, profile)
    if profile.minimum_salary is not None and job.salary_max is not None:
        preference_components.append(
            PreferenceScoreComponent(
                name="Salary",
                score=100.0 if salary_match else 0.0,
                weight=15.0,
                explanation=(
                    "Salary constraint met"
                    if salary_match
                    else "Salary maximum is below the minimum"
                ),
            )
        )
        reasons.append("Salary constraint met" if salary_match else "Salary maximum is below the minimum")
        if not salary_match:
            rejected.append("Advertised salary maximum is below the required minimum")
    elif profile.minimum_salary is not None:
        reasons.append("Salary was not published and was not included in Preference Fit")

    rejected.extend(_eligibility_blockers(job, profile))

    possible = sum(component.weight for component in preference_components)
    earned = sum(
        component.weight * component.score / 100.0
        for component in preference_components
    )
    preference_score = round(100 * earned / possible, 2) if possible else 100.0
    freshness_score, freshness_explanation = _posting_freshness(job, evaluated_at)
    return StageOneEvaluation(
        job=job,
        passed=not rejected,
        preference_score=preference_score,
        freshness_score=freshness_score,
        preference_components=tuple(preference_components),
        freshness_explanation=freshness_explanation,
        reasons=tuple(reasons),
        rejection_reasons=tuple(dict.fromkeys(rejected)),
    )


def rank_jobs(jobs: list[DiscoveredJob], profile: CandidateJobProfile) -> list[RankedJob]:
    """Deterministic compatibility helper used outside the two-stage service."""

    ranked = []
    for job in jobs:
        stage_one = evaluate_stage_one(job, profile)
        if not stage_one.passed:
            continue
        ranked.append(_score_structured_job(job, profile, stage_one=stage_one))
    return _sort_ranked(ranked)


def assess_analyzed_job(
    job: DiscoveredJob,
    profile: CandidateJobProfile,
    analysis: JobAnalysis,
    *,
    stage_one: StageOneEvaluation | None = None,
) -> RankedJob:
    """Score an AI-structured posting against verified evidence only."""

    requirements = _evidence_fit_requirements(analysis.requirements)
    statuses, evidence_matches = match_discovery_requirements(requirements, profile)
    assessment = build_requirement_fit_assessment(
        requirements,
        statuses,
        confirmation_complete=True,
        stage_label="Evidence-grounded discovery assessment",
    )
    return _ranked_from_assessment(
        job,
        profile,
        assessment,
        stage_one=stage_one,
        evidence_matches=evidence_matches,
    )


def ranked_from_snapshot(
    job: DiscoveredJob,
    snapshot: JobFitSnapshot,
    *,
    stage_one: StageOneEvaluation | None = None,
) -> RankedJob:
    reasons = tuple(
        [
            f"{match.requirement} — {match.evidence[0].label} [{match.evidence[0].record_id}]"
            for match in snapshot.evidence_matches[:3]
        ]
        + list(snapshot.hard_blockers[:2])
    )
    return RankedJob(
        job=job,
        fit_score=snapshot.fit_score,
        preference_score=stage_one.preference_score if stage_one else 100.0,
        freshness_score=stage_one.freshness_score if stage_one else 50.0,
        search_priority=_search_priority(
            snapshot.fit_score,
            stage_one.preference_score if stage_one else 100.0,
            stage_one.freshness_score if stage_one else 50.0,
        ),
        priority_formula=SEARCH_PRIORITY_FORMULA,
        reasons=reasons,
        fit_snapshot=snapshot,
        assessment=None,
        stage_one=stage_one,
        cache_hit=True,
    )


def build_discovery_requirements(job: DiscoveredJob) -> tuple[DiscoveryRequirement, ...]:
    """Build deterministic requirements from structured public posting data."""

    requirements: list[DiscoveryRequirement] = []
    seen: set[str] = set()
    used_ids: set[str] = set()

    def add(
        text: object,
        *,
        category: RequirementCategory,
        priority: RequirementPriority,
        keywords: Iterable[object] = (),
        origin: str,
        explicit_id: object = "",
    ) -> None:
        normalized_text = " ".join(str(text or "").split())
        key = stable_text_key(normalized_text)
        if not key or key in seen:
            return
        seen.add(key)
        raw_id = " ".join(str(explicit_id or "").split())
        requirement_id = raw_id or _requirement_id(origin, normalized_text)
        if requirement_id in used_ids:
            requirement_id = _requirement_id(f"{origin}:{requirement_id}", normalized_text)
        used_ids.add(requirement_id)
        requirements.append(
            DiscoveryRequirement(
                id=requirement_id,
                category=category,
                priority=priority,
                requirement=normalized_text,
                keywords=tuple(
                    value
                    for raw in keywords or ()
                    if (value := " ".join(str(raw or "").split()))
                ),
                origin=origin,
            )
        )

    structured = job.metadata.get("requirements", ())
    structured_items = (structured,) if isinstance(structured, Mapping) else _as_values(structured)
    for item in structured_items:
        if isinstance(item, Mapping):
            text = item.get("requirement") or item.get("text") or item.get("name")
            add(
                text,
                category=_category(item.get("category")),
                priority=_priority(item.get("priority")),
                keywords=_as_values(item.get("keywords")),
                origin="structured",
                explicit_id=item.get("id", ""),
            )
        else:
            add(item, category="responsibility", priority="important", origin="structured")

    for skill in job.skills:
        add(
            skill,
            category="technical_skill",
            priority="important",
            keywords=(skill,),
            origin="skill",
        )
    for field_name in ("education_requirements", "experience_requirements"):
        for value in _as_values(job.metadata.get(field_name)):
            add(
                value,
                category="qualification",
                priority="critical",
                origin=field_name,
            )
    return tuple(requirements)


def match_discovery_requirements(
    requirements: Iterable[ScorableRequirement],
    profile: CandidateJobProfile,
) -> tuple[dict[str, RequirementStatus], tuple[RequirementEvidenceMatch, ...]]:
    """Match requirements only against candidate-owned verified records.

    Job-description text and extracted posting keywords are used solely to
    describe the requirement being tested. They are never treated as evidence.
    Displayable strengths are emitted only when at least one traceable resume
    source or confirmed Evidence Library record supports the status.
    """

    verified_skill_keys = {
        stable_text_key(skill)
        for skill in (
            *profile.verified_skills,
            *profile.security_clearances,
            *profile.licenses_certifications,
        )
        if stable_text_key(skill)
    }
    evidence_keys = tuple(
        stable_text_key(statement)
        for statement in profile.evidence_statements
        if stable_text_key(statement)
    )
    evidence_corpus = " ".join(evidence_keys)
    reference_keys = tuple(
        (reference, stable_text_key(reference.statement))
        for reference in profile.evidence_references
        if stable_text_key(reference.statement)
    )

    statuses: dict[str, RequirementStatus] = {}
    matches: list[RequirementEvidenceMatch] = []
    for requirement in requirements:
        requirement_key = stable_text_key(requirement.requirement)
        keywords = tuple(getattr(requirement, "keywords", ()) or ())
        candidate_keys = tuple(
            dict.fromkeys(
                value
                for value in (
                    requirement_key,
                    *(stable_text_key(keyword) for keyword in keywords),
                )
                if value
            )
        )

        matched_references, traced_status = _traceable_requirement_evidence(
            requirement_key,
            candidate_keys,
            reference_keys,
        )

        if reference_keys:
            # Production discovery profiles carry record-level provenance. In
            # that mode, the score follows the conservative traced match and
            # cannot be upgraded merely because a posting keyword also appears
            # in a flattened string collection.
            status: RequirementStatus = traced_status
        elif any(candidate in verified_skill_keys for candidate in candidate_keys):
            status = "supported"
        elif any(
            candidate and (candidate in evidence_corpus or candidate in evidence_keys)
            for candidate in candidate_keys
        ):
            status = "supported"
        else:
            comparison_pool = tuple(verified_skill_keys) + evidence_keys
            status = _best_status(requirement_key, comparison_pool)

        statuses[requirement.id] = status

        display_status = traced_status if traced_status in {"supported", "partial"} else None
        if matched_references and display_status is not None:
            matches.append(
                RequirementEvidenceMatch(
                    requirement_id=requirement.id,
                    requirement=requirement.requirement,
                    status=display_status,
                    evidence=matched_references,
                )
            )

    return statuses, tuple(matches)


def build_discovery_requirement_statuses(
    requirements: Iterable[ScorableRequirement],
    profile: CandidateJobProfile,
) -> dict[str, RequirementStatus]:
    """Compatibility wrapper returning only the shared scorer statuses."""

    statuses, _ = match_discovery_requirements(requirements, profile)
    return statuses


def _traceable_requirement_evidence(
    requirement_key: str,
    candidate_keys: tuple[str, ...],
    reference_keys: tuple[tuple[EvidenceReference, str], ...],
) -> tuple[tuple[EvidenceReference, ...], RequirementStatus]:
    requirement_tokens = set(requirement_key.split()) - _GENERIC_REQUIREMENT_TOKENS
    if not requirement_tokens or not reference_keys:
        return (), "unsupported"

    scored: list[tuple[float, float, EvidenceReference]] = []
    union_evidence_tokens: set[str] = set()
    for reference, reference_key in reference_keys:
        reference_tokens = set(reference_key.split())
        requirement_coverage = (
            len(requirement_tokens & reference_tokens) / len(requirement_tokens)
        )
        keyword_overlap = max(
            (
                len(set(candidate.split()) & reference_tokens)
                / max(1, len(set(candidate.split())))
                for candidate in candidate_keys[1:]
                if candidate
            ),
            default=0.0,
        )
        exact_atomic_match = (
            len(requirement_tokens) <= 3
            and any(
                candidate == reference_key
                or candidate in reference_key
                or reference_key in candidate
                for candidate in candidate_keys
                if candidate
            )
        )
        relevance = max(requirement_coverage, keyword_overlap)
        if exact_atomic_match:
            relevance = 1.0
        if relevance < 0.34:
            continue
        union_evidence_tokens.update(reference_tokens)
        scored.append((relevance, requirement_coverage, reference))

    if not scored:
        return (), "unsupported"

    combined_coverage = len(requirement_tokens & union_evidence_tokens) / len(
        requirement_tokens
    )
    atomic = len(requirement_tokens) <= 3
    has_exact_atomic = atomic and any(score[0] >= 1.0 for score in scored)
    if has_exact_atomic or combined_coverage >= 0.8:
        status: RequirementStatus = "supported"
    elif combined_coverage >= 0.4 or any(score[0] >= 0.7 for score in scored):
        status = "partial"
    else:
        return (), "unsupported"

    scored.sort(
        key=lambda item: (item[0], item[1], item[2].label.casefold()),
        reverse=True,
    )
    return tuple(item[2] for item in scored[:3]), status



def _status_rank(status: RequirementStatus) -> int:
    return {"unsupported": 0, "partial": 1, "supported": 2}[status]



def _score_structured_job(
    job: DiscoveredJob,
    profile: CandidateJobProfile,
    *,
    stage_one: StageOneEvaluation | None = None,
) -> RankedJob:
    requirements = _evidence_fit_requirements(build_discovery_requirements(job))
    statuses, evidence_matches = match_discovery_requirements(requirements, profile)
    assessment = build_requirement_fit_assessment(
        requirements,
        statuses,
        confirmation_complete=True,
        stage_label="Deterministic discovery assessment",
    )
    return _ranked_from_assessment(
        job,
        profile,
        assessment,
        stage_one=stage_one,
        evidence_matches=evidence_matches,
    )


def _ranked_from_assessment(
    job: DiscoveredJob,
    profile: CandidateJobProfile,
    assessment: ApplicationFitAssessment,
    *,
    stage_one: StageOneEvaluation | None,
    evidence_matches: tuple[RequirementEvidenceMatch, ...] = (),
) -> RankedJob:
    reasons: list[str] = [
        f"{match.requirement} — {match.evidence[0].label} [{match.evidence[0].record_id}]"
        for match in evidence_matches[:3]
    ]
    reasons.extend(assessment.obstacles[: max(0, 5 - len(reasons))])
    snapshot = JobFitSnapshot(
        job_id=job.id,
        owner_id=job.owner_id,
        profile_fingerprint=profile.fingerprint,
        description_fingerprint=job.description_fingerprint,
        fit_score=assessment.score,
        recommendation=assessment.recommendation,
        confidence=assessment.confidence,
        supported_requirements=assessment.supported_requirements,
        partial_requirements=assessment.partial_requirements,
        unsupported_requirements=assessment.unsupported_requirements,
        hard_blockers=assessment.hard_blockers,
        evidence_matches=evidence_matches,
    )
    return RankedJob(
        job=job,
        fit_score=assessment.score,
        preference_score=stage_one.preference_score if stage_one else 100.0,
        freshness_score=stage_one.freshness_score if stage_one else 50.0,
        search_priority=_search_priority(
            assessment.score,
            stage_one.preference_score if stage_one else 100.0,
            stage_one.freshness_score if stage_one else 50.0,
        ),
        priority_formula=SEARCH_PRIORITY_FORMULA,
        reasons=tuple(reasons),
        fit_snapshot=snapshot,
        assessment=assessment,
        stage_one=stage_one,
    )


def _sort_ranked(ranked: list[RankedJob]) -> list[RankedJob]:
    return sorted(
        ranked,
        key=lambda item: (
            item.search_priority,
            item.fit_score,
            item.job.posted_at,
            item.job.title.casefold(),
        ),
        reverse=True,
    )


def _search_priority(
    fit_score: float,
    preference_score: float,
    freshness_score: float,
) -> float:
    return round(
        float(fit_score) * SEARCH_PRIORITY_FIT_WEIGHT
        + float(preference_score) * SEARCH_PRIORITY_PREFERENCE_WEIGHT
        + float(freshness_score) * SEARCH_PRIORITY_FRESHNESS_WEIGHT,
        2,
    )


def _evidence_fit_requirements(
    requirements: Iterable[ScorableRequirement],
) -> tuple[ScorableRequirement, ...]:
    """Exclude search-preference facts from the evidence-grounded fit score."""

    return tuple(
        requirement
        for requirement in requirements
        if not _is_preference_only_requirement(requirement.requirement)
    )


def _is_preference_only_requirement(requirement: str) -> bool:
    text = " ".join(str(requirement or "").split()).casefold()
    if text in {
        "remote",
        "hybrid",
        "onsite",
        "on-site",
        "full time",
        "full-time",
        "part time",
        "part-time",
        "contract",
        "temporary",
    }:
        return True
    return any(re.search(pattern, text) for pattern in _PREFERENCE_ONLY_REQUIREMENT_PATTERNS)


def _posting_freshness(
    job: DiscoveredJob,
    evaluated_at: datetime | str | None,
) -> tuple[float, str]:
    reference = _as_utc_datetime(evaluated_at) or datetime.now(timezone.utc)
    published = _as_utc_datetime(job.posted_at) or _as_utc_datetime(job.first_seen_at)
    if published is None:
        return 50.0, "Posting date is unavailable; freshness is neutral"
    age_days = max(0.0, (reference - published).total_seconds() / 86400.0)
    if age_days <= 1:
        return 100.0, "Posted within the last day"
    if age_days <= 3:
        return 95.0, "Posted within the last three days"
    if age_days <= 7:
        return 85.0, "Posted within the last week"
    if age_days <= 14:
        return 70.0, "Posted within the last two weeks"
    if age_days <= 30:
        return 50.0, "Posted within the last month"
    if age_days <= 60:
        return 25.0, "Posting is one to two months old"
    return 10.0, "Posting is more than two months old"


def _as_utc_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("ranking timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _best_overlap(value: str, candidates: Iterable[str]) -> float:
    key = stable_text_key(value)
    return max((_token_overlap(key, stable_text_key(item)) for item in candidates), default=0.0)


def _skill_overlap(job: DiscoveredJob, profile: CandidateJobProfile) -> float:
    profile_skills = tuple(stable_text_key(value) for value in profile.verified_skills)
    job_skills = tuple(stable_text_key(value) for value in job.skills)
    if not job_skills:
        description = stable_text_key(job.description)
        matched = sum(1 for skill in profile_skills if skill and skill in description)
        return matched / len(profile_skills) if profile_skills else 0.0
    matched = sum(
        1
        for skill in job_skills
        if any(_token_overlap(skill, candidate) >= 0.8 for candidate in profile_skills)
    )
    return matched / len(job_skills) if job_skills else 0.0


def _location_match(job: DiscoveredJob, profile: CandidateJobProfile) -> bool:
    if job.workplace_type is WorkplaceType.REMOTE and profile.accepts_remote:
        return True
    locations = tuple(job.locations) or ((job.location,) if job.location else ())
    return any(
        _location_contains(location, preference)
        for location in locations
        for preference in profile.preferred_locations
    )


def _location_contains(location: str, preference: str) -> bool:
    left = stable_text_key(location)
    right = stable_text_key(preference)
    return bool(left and right and (left in right or right in left))


def _workplace_match(job: DiscoveredJob, profile: CandidateJobProfile) -> bool:
    if job.workplace_type is WorkplaceType.REMOTE and not profile.accepts_remote:
        return False
    if not profile.accepted_workplace_types:
        return True
    if job.workplace_type is WorkplaceType.UNSPECIFIED:
        return not profile.require_workplace_match
    return job.workplace_type in profile.accepted_workplace_types


def _employment_type_match(job: DiscoveredJob, profile: CandidateJobProfile) -> bool:
    if not profile.preferred_employment_types:
        return True
    current = stable_text_key(job.employment_type)
    if not current:
        return not profile.require_employment_type_match
    return any(
        current in stable_text_key(value) or stable_text_key(value) in current
        for value in profile.preferred_employment_types
    )


def _salary_match(job: DiscoveredJob, profile: CandidateJobProfile) -> bool:
    if profile.minimum_salary is None or job.salary_max is None:
        return True
    if (
        profile.minimum_salary_currency
        and job.salary_currency
        and profile.minimum_salary_currency != job.salary_currency.upper()
    ):
        return True
    annual_max = _annual_salary(job.salary_max, job.salary_interval)
    annual_minimum = _annual_salary(profile.minimum_salary, profile.minimum_salary_interval)
    return annual_max >= annual_minimum


def _annual_salary(value: float, interval: str) -> float:
    normalized = stable_text_key(interval)
    multiplier = {
        "hour": 2080,
        "hourly": 2080,
        "week": 52,
        "weekly": 52,
        "month": 12,
        "monthly": 12,
        "year": 1,
        "yearly": 1,
        "annual": 1,
        "annually": 1,
    }.get(normalized, 1)
    return float(value) * multiplier


def _eligibility_blockers(job: DiscoveredJob, profile: CandidateJobProfile) -> list[str]:
    text = " ".join((job.title, job.description)).casefold()
    blockers: list[str] = []
    if profile.requires_sponsorship is True and _matches_any(text, _NO_SPONSORSHIP_PATTERNS):
        blockers.append("Posting states that visa sponsorship is unavailable")
    if profile.work_authorized is False and _matches_any(text, _WORK_AUTHORIZATION_PATTERNS):
        blockers.append("Posting requires current work authorization")
    if profile.us_citizen is False and _matches_any(text, _US_CITIZEN_PATTERNS):
        blockers.append("Posting requires U.S. citizenship")
    if profile.eligibility_profile_complete and _matches_any(text, _CLEARANCE_PATTERNS):
        if not profile.security_clearances:
            blockers.append("Posting requires a security clearance not present in the verified profile")
    if profile.eligibility_profile_complete and _matches_any(text, _LICENSE_PATTERNS):
        verified = " ".join(profile.licenses_certifications).casefold()
        if "license" not in verified and "licence" not in verified:
            blockers.append("Posting requires a professional license not present in the verified profile")
    return blockers


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _requirement_id(origin: str, text: str) -> str:
    digest = hashlib.sha256(f"{origin}\0{text.casefold()}".encode("utf-8")).hexdigest()
    return f"discovery-{digest[:16]}"


def _priority(value: object) -> RequirementPriority:
    normalized = stable_text_key(str(value or ""))
    return normalized if normalized in {"critical", "important", "secondary"} else "important"  # type: ignore[return-value]


def _category(value: object) -> RequirementCategory:
    normalized = stable_text_key(str(value or "")).replace(" ", "_")
    allowed = {
        "technical_skill",
        "domain_knowledge",
        "methodology",
        "responsibility",
        "leadership",
        "qualification",
    }
    return normalized if normalized in allowed else "responsibility"  # type: ignore[return-value]


def _as_values(value: Any) -> tuple[object, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    if isinstance(value, Mapping):
        return tuple(value.values())
    return (value,)


def _best_status(text_key: str, candidates: tuple[str, ...]) -> RequirementStatus:
    if not text_key or not candidates:
        return "unsupported"
    requirement_tokens = set(text_key.split())
    substantive_tokens = requirement_tokens - _GENERIC_REQUIREMENT_TOKENS
    for candidate in candidates:
        candidate_tokens = set(candidate.split())
        if substantive_tokens and substantive_tokens.issubset(candidate_tokens):
            return "supported"
    best = max((_token_overlap(text_key, candidate) for candidate in candidates), default=0.0)
    if best >= 0.8:
        return "supported"
    if best >= 0.4:
        return "partial"
    return "unsupported"


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return max(intersection / len(left_tokens), intersection / len(right_tokens))


def _clean_values(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = " ".join(str(raw or "").split())
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)
