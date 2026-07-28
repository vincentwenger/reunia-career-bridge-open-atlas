from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactInfo(StrictModel):
    location: str
    phone: str
    email: str
    linkedin_label: str = "LinkedIn"
    linkedin_url: str = ""
    github_label: str = "GitHub"
    github_url: str = ""


class EducationItem(StrictModel):
    credential: str
    institution: str
    location: str = ""
    date: str
    detail: str = ""


class ResumeBullet(StrictModel):
    id: str
    text: str


class Experience(StrictModel):
    id: str
    employer: str
    location: str
    dates: str
    title: str
    bullets: list[ResumeBullet]


class VerifiedSkills(StrictModel):
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    tools_software: list[str] = Field(default_factory=list)
    industry_knowledge: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    def all_non_language_skills(self) -> list[str]:
        return (
            self.hard_skills
            + self.soft_skills
            + self.tools_software
            + self.industry_knowledge
        )


ConfirmationPlacement = Literal["auto", "update_existing", "new_bullet"]


class SupplementalEvidence(StrictModel):
    id: str
    statement: str
    requirement_ids: list[str] = Field(default_factory=list)
    verified_skills: list[str] = Field(default_factory=list)
    source: str = "candidate_confirmation"
    experience_id: str = ""
    source_bullet_id: str = ""
    placement: ConfirmationPlacement = "auto"


class CandidateProfile(StrictModel):
    name: str
    contact: ContactInfo
    current_summary: str
    skills: VerifiedSkills
    education: list[EducationItem]
    experiences: list[Experience]
    supplemental_evidence: list[SupplementalEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> "CandidateProfile":
        experience_ids = [experience.id for experience in self.experiences]
        if len(experience_ids) != len(set(experience_ids)):
            raise ValueError("Experience IDs must be unique.")
        bullet_ids = [
            bullet.id
            for experience in self.experiences
            for bullet in experience.bullets
        ]
        if len(bullet_ids) != len(set(bullet_ids)):
            raise ValueError("Source bullet IDs must be unique across all experiences.")
        evidence_ids = [item.id for item in self.supplemental_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Supplemental evidence IDs must be unique.")
        if set(evidence_ids) & set(bullet_ids):
            raise ValueError("Supplemental evidence IDs must not duplicate source bullet IDs.")
        return self

    def bullet_lookup(self) -> dict[str, str]:
        return {
            bullet.id: bullet.text
            for experience in self.experiences
            for bullet in experience.bullets
        }

    def experience_lookup(self) -> dict[str, Experience]:
        return {experience.id: experience for experience in self.experiences}

    def all_source_text(self) -> str:
        parts = [self.current_summary]
        parts.extend(self.skills.all_non_language_skills())
        parts.extend(self.skills.languages)
        for experience in self.experiences:
            parts.extend(
                [experience.employer, experience.location, experience.dates, experience.title]
            )
            parts.extend(bullet.text for bullet in experience.bullets)
        for education in self.education:
            parts.extend(
                [
                    education.credential,
                    education.institution,
                    education.location,
                    education.date,
                    education.detail,
                ]
            )
        for evidence in self.supplemental_evidence:
            parts.append(evidence.statement)
            parts.extend(evidence.verified_skills)
        return "\n".join(part for part in parts if part)

    def all_verified_skills(self) -> list[str]:
        skills = list(self.skills.all_non_language_skills())
        for evidence in self.supplemental_evidence:
            skills.extend(evidence.verified_skills)
        return skills


RequirementCategory = Literal[
    "technical_skill",
    "domain_knowledge",
    "methodology",
    "responsibility",
    "leadership",
    "qualification",
]
RequirementPriority = Literal["critical", "important", "secondary"]


class JobRequirement(StrictModel):
    id: str
    category: RequirementCategory
    priority: RequirementPriority
    requirement: str
    keywords: list[str] = Field(default_factory=list)


class JobAnalysis(StrictModel):
    target_title: str
    target_company: str = ""
    requirements: list[JobRequirement]
    ignored_boilerplate: list[str] = Field(default_factory=list)

    @field_validator("requirements")
    @classmethod
    def unique_requirement_ids(cls, value: list[JobRequirement]) -> list[JobRequirement]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique.")
        return value


EvidenceStatus = Literal["supported", "partial", "unsupported"]


class EvidenceMatch(StrictModel):
    requirement_id: str
    status: EvidenceStatus
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str


class SkillSet(StrictModel):
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    tools_software: list[str] = Field(default_factory=list)
    industry_knowledge: list[str] = Field(default_factory=list)

    def total_count(self) -> int:
        return sum(
            len(items)
            for items in (
                self.hard_skills,
                self.soft_skills,
                self.tools_software,
                self.industry_knowledge,
            )
        )


class BulletProposal(StrictModel):
    source_bullet_id: str
    include: bool
    proposed_text: str
    matched_requirement_ids: list[str] = Field(default_factory=list)
    evidence_note: str


QuestionAnswerType = Literal[
    "yes_no",
    "yes_no_with_details",
    "short_text",
    "long_text",
    "number",
    "date_or_range",
]


class CandidateQuestion(StrictModel):
    id: str
    requirement_id: str = ""
    source_id: str = ""
    question: str
    answer_type: QuestionAnswerType
    details_prompt: str = ""
    help_text: str = ""
    required: bool = True


class CandidateAnswer(StrictModel):
    question_id: str
    question: str = ""
    requirement_id: str = ""
    answer_type: QuestionAnswerType
    yes_no: bool | None = None
    text: str = ""
    experience_id: str = ""
    placement: ConfirmationPlacement = "auto"


class TailoringProposal(StrictModel):
    professional_summary: str
    skills: SkillSet
    bullet_proposals: list[BulletProposal]
    evidence_matches: list[EvidenceMatch]
    unsupported_requirements: list[str] = Field(default_factory=list)
    candidate_questions: list[CandidateQuestion] = Field(default_factory=list)


IssueSeverity = Literal["blocking", "warning"]


class AuditIssue(StrictModel):
    severity: IssueSeverity
    section: str
    source_id: str = ""
    issue: str
    suggested_fix: str = ""


class ProposalAudit(StrictModel):
    passed: bool
    issues: list[AuditIssue] = Field(default_factory=list)
    verified_strengths: list[str] = Field(default_factory=list)


class ApprovedResume(StrictModel):
    target_title: str
    professional_summary: str
    skills: SkillSet
    bullets_by_experience: dict[str, list[str]]
