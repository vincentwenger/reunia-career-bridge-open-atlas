from __future__ import annotations

import re
from collections import Counter

from .grounding import validate_candidate_claim
from .models import (
    CandidateProfile,
    CareerTranslationAssessment,
    CareerTranslationFinding,
    JobAnalysis,
    NewcomerCareerProfile,
    TailoringProposal,
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ž0-9+#.-]+")
_STOP_WORDS = {
    "and",
    "the",
    "with",
    "for",
    "from",
    "that",
    "this",
    "into",
    "using",
    "role",
    "work",
    "years",
    "experience",
    "skills",
    "knowledge",
    "ability",
    "required",
    "preferred",
}

_UNTRACEABLE_INTERPRETATION_MESSAGE = (
    "The generated interpretation was not fully traceable to the cited Verified Resume Evidence."
)
_CANDIDATE_CONFIRMATION_MEANING = (
    "This proposed translation or interpretation requires candidate confirmation "
    "before it can shape resume or interview wording."
)

_GENERIC_APPLICATION_DEVELOPMENT_REQUIREMENTS = {
    "application development",
    "software development",
    "application engineering",
    "software engineering",
    "develop applications",
    "develop software",
    "developing applications",
    "developing software",
}
_APPLICATION_DEVELOPMENT_ROLE_MARKERS = (
    "software engineer",
    "software developer",
    "application developer",
    "application engineer",
    "backend engineer",
    "front-end engineer",
    "frontend engineer",
    "full-stack engineer",
    "full stack engineer",
    "developer",
    "programmer",
    "systems analyst",
)
_APPLICATION_DEVELOPMENT_ACTION_RE = re.compile(
    r"\b(?:architect(?:ed)?|automat(?:e|ed)|build|built|cod(?:e|ed)|creat(?:e|ed)|"
    r"deliver(?:ed)?|design(?:ed)?|develop(?:ed)?|enhanc(?:e|ed)|engineer(?:ed)?|"
    r"implement(?:ed)?|integrat(?:e|ed)|maintain(?:ed)?|migrat(?:e|ed)|program(?:med)?|"
    r"refactor(?:ed)?|releas(?:e|ed)|test(?:ed)?|writ(?:e|ten|ing))\b",
    re.IGNORECASE,
)
_APPLICATION_DEVELOPMENT_OBJECT_RE = re.compile(
    r"\b(?:api|apis|application|applications|backend|code|database|databases|feature|"
    r"features|integration|integrations|platform|platforms|program|programs|report|reports|"
    r"service|services|software|solution|solutions|system|systems|workflow|workflows)\b",
    re.IGNORECASE,
)
_APPLICATION_DEVELOPMENT_STRONG_ACTION_RE = re.compile(
    r"\b(?:architect(?:ed)?|automat(?:e|ed)|build|built|cod(?:e|ed)|creat(?:e|ed)|"
    r"design(?:ed)?|develop(?:ed)?|enhanc(?:e|ed)|engineer(?:ed)?|implement(?:ed)?|"
    r"integrat(?:e|ed)|maintain(?:ed)?|migrat(?:e|ed)|program(?:med)?|refactor(?:ed)?)\b",
    re.IGNORECASE,
)
_APPLICATION_DEVELOPMENT_STRONG_OBJECT_RE = re.compile(
    r"\b(?:api|apis|application|applications|backend|code|database|databases|feature|"
    r"features|integration|integrations|platform|platforms|service|services|software|"
    r"solution|solutions|system|systems|workflow|workflows)\b",
    re.IGNORECASE,
)

_GENERIC_TECHNICAL_DOCUMENTATION_REQUIREMENTS = {
    "create and maintain technical documentation",
    "create technical documentation",
    "maintain technical documentation",
    "technical documentation",
    "produce technical documentation",
}
_TECHNICAL_DOCUMENTATION_RE = re.compile(
    r"\b(?:creat(?:e|ed|ing)|document(?:ed|ing)?|generat(?:e|ed|ing)|maintain(?:ed|ing)?|"
    r"produc(?:e|ed|ing)|writ(?:e|ten|ing))\b.{0,80}\b(?:api documentation|"
    r"deployment procedures?|design documents?|documentation|release documentation|"
    r"runbooks?|specifications?|technical documents?|technical reports?|test scenarios?|"
    r"user guides?)\b",
    re.IGNORECASE,
)

_GENERIC_PRODUCTION_TROUBLESHOOTING_REQUIREMENTS = {
    "troubleshoot production issues",
    "troubleshooting production issues",
    "production troubleshooting",
    "resolve production issues",
    "diagnose production issues",
    "production support",
}
_PRODUCTION_TROUBLESHOOTING_RE = re.compile(
    r"\b(?:analy(?:s|z)(?:e|ed|ing)|debug(?:ged|ging)?|diagnos(?:e|ed|ing)|"
    r"investigat(?:e|ed|ing)|resolv(?:e|ed|ing)|support(?:ed|ing)?|"
    r"troubleshoot(?:ed|ing)?)\b.{0,100}\b(?:client-reported technical issues?|"
    r"defects?|failures?|incidents?|issues?|logs?|production|root cause|stability)\b|"
    r"\b(?:client-reported technical issues?|production incidents?|production issues?)\b"
    r".{0,100}\b(?:diagnos(?:e|ed)|resolv(?:e|ed)|support(?:ed)?|troubleshoot(?:ed)?)\b",
    re.IGNORECASE,
)

_GENERIC_COLLABORATION_REQUIREMENTS = {
    "strong collaboration skills",
    "strong collaboration",
    "collaboration skills",
    "collaboration",
    "cross-functional collaboration",
    "work collaboratively",
    "collaborate with cross-functional teams",
}
_COLLABORATION_RE = re.compile(
    r"\b(?:collaborat(?:e|ed|ing)|cross-functional|partner(?:ed|ing)?|"
    r"stakeholder communication|team collaboration|worked with)\b",
    re.IGNORECASE,
)

_GENERIC_REQUIREMENTS_TRANSLATION_REQUIREMENTS = {
    "analyze data requirements and translate business needs into technical solutions",
    "translate business requirements into technical solutions",
    "analyze requirements and design technical solutions",
    "requirements analysis",
    "business requirements translation",
}
_REQUIREMENTS_TRANSLATION_RE = re.compile(
    r"\b(?:analy(?:s|z)(?:e|ed|ing)|gather(?:ed|ing)?|translate(?:d|ing)?|"
    r"understand(?:ing)?)\b.{0,100}\b(?:business needs?|business requirements?|"
    r"client requirements?|data requirements?|financial requirements?|requirements?)\b"
    r".{0,120}\b(?:application|design|implement(?:ation)?|solution|software|system|"
    r"technical|workflow)\b|\b(?:requirements gathering|requirements analysis)\b",
    re.IGNORECASE,
)

_GENERIC_REUSABLE_DATA_COMPONENT_REQUIREMENTS = {
    "develop scalable and reusable data components",
    "build scalable and reusable data components",
    "scalable and reusable data components",
    "reusable data components",
}
_REUSABLE_DATA_COMPONENT_RE = re.compile(
    r"\b(?:build|built|creat(?:e|ed)|design(?:ed)?|develop(?:ed)?|implement(?:ed)?)\b"
    r".{0,100}\b(?:configurable|framework|library|module|package|reusable|scalable|"
    r"shared|template)\b.{0,80}\b(?:component|data|etl|pipeline|procedure|report|"
    r"solution|workflow)\b|\b(?:configurable|reusable|scalable|shared)\b.{0,80}"
    r"\b(?:data components?|etl workflows?|pipelines?|procedures?|reporting components?)\b",
    re.IGNORECASE,
)

_GENERIC_PERFORMANCE_OPTIMIZATION_REQUIREMENTS = {
    "performance optimization",
    "optimize performance",
    "application performance optimization",
    "database performance optimization",
    "system performance optimization",
}
_PERFORMANCE_OPTIMIZATION_RE = re.compile(
    r"\b(?:diagnos(?:e|ed|ing)|improv(?:e|ed|ing)|optimi[sz](?:e|ed|ing)|"
    r"tun(?:e|ed|ing))\b.{0,100}\b(?:database|execution|latency|performance|query|"
    r"queries|response time|sql|throughput|workflow)\b|\bperformance optimization\b",
    re.IGNORECASE,
)

_GENERIC_DATA_ARCHITECTURE_REQUIREMENTS = {
    "data architecture",
    "data architecture principles",
    "familiarity with data architecture principles",
    "knowledge of data architecture principles",
    "understanding of data architecture principles",
}
_DATA_ARCHITECTURE_RE = re.compile(
    r"\b(?:architect(?:ed|ing|ure)?|design(?:ed|ing)?|model(?:ed|ing)?|"
    r"redesign(?:ed|ing)?)\b.{0,120}\b(?:data|database|etl|integration|pipeline|"
    r"reporting|solution|system|workflow)\b|\b(?:data integration|data modeling|"
    r"etl workflows?|multiple data sources?|database systems?)\b",
    re.IGNORECASE,
)

_GENERIC_PROCESS_AUTOMATION_REQUIREMENTS = {
    "identify opportunities to improve processes and automation",
    "identify process improvement and automation opportunities",
    "improve processes and automation",
    "process improvement and automation",
    "process automation",
}
_PROCESS_AUTOMATION_RE = re.compile(
    r"\b(?:automat(?:e|ed|ing|ion)|improv(?:e|ed|ing)|optimi[sz](?:e|ed|ing)|"
    r"streamlin(?:e|ed|ing))\b.{0,120}\b(?:ci/cd|deployment|efficiency|pipeline|"
    r"process|release|testing|validation|workflow)\b|\bautomated (?:code validation|"
    r"deployment|pipeline|regression testing|release)\b",
    re.IGNORECASE,
)

_GENERIC_ANALYTICAL_PROBLEM_SOLVING_REQUIREMENTS = {
    "analytical thinking",
    "analytical skills",
    "analyze complex data problems",
    "analyse complex data problems",
    "complex data problem solving",
    "problem solving",
}
_ANALYTICAL_PROBLEM_SOLVING_RE = re.compile(
    r"\b(?:analy(?:s|z)(?:e|ed|ing)|diagnos(?:e|ed|ing)|evaluat(?:e|ed|ing)|"
    r"investigat(?:e|ed|ing)|resolv(?:e|ed|ing)|transform(?:ed|ing)?|"
    r"troubleshoot(?:ed|ing)?|validat(?:e|ed|ing))\b.{0,120}\b(?:data|defect|"
    r"issue|problem|query|report|requirement|risk|sql|system|workflow)\b",
    re.IGNORECASE,
)

_GENERIC_RESILIENCY_TUNING_REQUIREMENTS = {
    "contribute to platform resiliency and performance tuning",
    "platform resiliency and performance tuning",
    "platform resiliency",
    "system resiliency and performance tuning",
}
_RESILIENCY_TUNING_RE = re.compile(
    r"\b(?:enhanc(?:e|ed|ing)|improv(?:e|ed|ing)|optimi[sz](?:e|ed|ing)|"
    r"redesign(?:ed|ing)?|stabili[sz](?:e|ed|ing)|tun(?:e|ed|ing))\b.{0,140}"
    r"\b(?:architecture|database|operational stability|performance|platform|query|"
    r"reliability|resilien(?:ce|cy)|sql|stability|system|workflow)\b|"
    r"\b(?:operational stability|system stability)\b",
    re.IGNORECASE,
)

_CAPABILITY_SKILL_PATTERNS: dict[str, tuple[str, ...]] = {
    "technical_documentation": (
        "technical documentation",
        "release documentation",
        "documentation",
    ),
    "production_troubleshooting": (
        "incident response",
        "issue resolution",
        "production support",
        "troubleshooting",
    ),
    "collaboration": (
        "cross-functional collaboration",
        "technical collaboration",
        "stakeholder communication",
        "team collaboration",
    ),
    "requirements_translation": (
        "requirements analysis",
        "requirements gathering",
        "business analysis",
    ),
    "reusable_data_components": (
        "reusable data components",
        "scalable data components",
    ),
    "performance_optimization": (
        "performance optimization",
        "query optimization",
        "sql tuning",
    ),
    "data_architecture": (
        "data architecture",
        "data integration",
        "data modeling",
        "etl workflows",
    ),
    "process_automation": (
        "automation",
        "ci/cd",
        "continuous integration",
        "process improvement",
    ),
    "analytical_problem_solving": (
        "analytical thinking",
        "data analysis",
        "data transformation",
        "issue resolution",
        "problem solving",
        "risk assessment",
    ),
    "resiliency_tuning": (
        "performance optimization",
        "production support",
        "sql tuning",
        "system reliability",
    ),
}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(value)
        if len(token) > 2 and token.casefold() not in _STOP_WORDS
    }


def _finding_key(finding: CareerTranslationFinding) -> tuple[str, str]:
    return finding.category, _normalized(finding.source_text)


def _generic_requirement_core(value: str) -> str:
    """Remove harmless wrappers while preserving requirement specificity."""

    core = _normalized(value)
    prefixes = (
        "demonstrated experience in ",
        "demonstrated experience with ",
        "professional experience in ",
        "professional experience with ",
        "experience in ",
        "experience with ",
        "knowledge of ",
        "proficiency in ",
        "background in ",
    )
    suffixes = (
        " experience",
        " expertise",
        " proficiency",
        " skills",
    )
    for prefix in prefixes:
        if core.startswith(prefix):
            core = core[len(prefix) :].strip()
            break
    for suffix in suffixes:
        if core.endswith(suffix):
            core = core[: -len(suffix)].strip()
            break
    return core


def _application_development_evidence_ids(
    profile: CandidateProfile,
    requirement_text: str,
) -> list[str]:
    """Find traceable evidence for a generic application-development requirement.

    This intentionally handles only broad umbrella wording. A requirement with a
    technology, platform, channel, domain, or other qualifier is left untouched so
    that, for example, mobile or Salesforce development is never inferred from
    general software-engineering experience.
    """

    if (
        _generic_requirement_core(requirement_text)
        not in _GENERIC_APPLICATION_DEVELOPMENT_REQUIREMENTS
    ):
        return []

    profile_skill_text = " ".join(profile.all_verified_skills())
    profile_summary = profile.current_summary
    profile_level_support = bool(
        _APPLICATION_DEVELOPMENT_OBJECT_RE.search(profile_skill_text)
        or _APPLICATION_DEVELOPMENT_ACTION_RE.search(profile_skill_text)
        or "software engineer" in _normalized(profile_summary)
        or "software developer" in _normalized(profile_summary)
        or "application developer" in _normalized(profile_summary)
    )

    evidence_ids: list[str] = []
    for experience in profile.experiences:
        normalized_title = _normalized(experience.title)
        role_support = any(
            marker in normalized_title
            for marker in _APPLICATION_DEVELOPMENT_ROLE_MARKERS
        )
        for bullet in experience.bullets:
            has_action = bool(_APPLICATION_DEVELOPMENT_ACTION_RE.search(bullet.text))
            has_object = bool(_APPLICATION_DEVELOPMENT_OBJECT_RE.search(bullet.text))
            strong_non_role_evidence = bool(
                _APPLICATION_DEVELOPMENT_STRONG_ACTION_RE.search(bullet.text)
                and _APPLICATION_DEVELOPMENT_STRONG_OBJECT_RE.search(bullet.text)
            )
            if has_action and has_object and (
                role_support or (profile_level_support and strong_non_role_evidence)
            ):
                evidence_ids.append(bullet.id)

    for item in profile.supplemental_evidence:
        evidence_text = " ".join((item.statement, *item.verified_skills))
        if (
            _APPLICATION_DEVELOPMENT_ACTION_RE.search(evidence_text)
            and _APPLICATION_DEVELOPMENT_OBJECT_RE.search(evidence_text)
        ):
            evidence_ids.append(item.id)

    return list(dict.fromkeys(evidence_ids))[:6]


def _pattern_capability_evidence_ids(
    profile: CandidateProfile,
    *,
    skill_key: str,
    evidence_pattern: re.Pattern[str],
) -> list[str]:
    """Return conservative evidence IDs for a broad, unqualified capability.

    A verified skill may establish that the capability exists, while specific
    bullets provide the traceable examples that should shape resume wording.
    The profile-level ID is included only when a matching verified skill exists.
    """

    evidence_ids: list[str] = []
    normalized_skills = {_normalized(item) for item in profile.all_verified_skills()}
    if any(
        _normalized(marker) in normalized_skills
        for marker in _CAPABILITY_SKILL_PATTERNS[skill_key]
    ):
        evidence_ids.append("CANDIDATE-PROFILE")

    for experience in profile.experiences:
        for bullet in experience.bullets:
            if evidence_pattern.search(bullet.text):
                evidence_ids.append(bullet.id)

    for item in profile.supplemental_evidence:
        evidence_text = " ".join((item.statement, *item.verified_skills))
        if evidence_pattern.search(evidence_text):
            evidence_ids.append(item.id)

    return list(dict.fromkeys(evidence_ids))[:6]


def _broad_capability_support(
    profile: CandidateProfile,
    requirement_text: str,
) -> tuple[list[str], str, str, str] | None:
    """Recognize only broad capability wording backed by traceable evidence.

    Qualified variants remain untouched. For example, general collaboration can
    be supported by documented cross-functional delivery, but Salesforce-specific
    collaboration or mobile-development experience is never inferred from it.
    """

    core = _generic_requirement_core(requirement_text)

    application_evidence = _application_development_evidence_ids(
        profile,
        requirement_text,
    )
    if application_evidence:
        return (
            application_evidence,
            "Broad application-development capability supported by documented software-engineering work.",
            "The requirement is generic, and the cited Verified Resume Evidence documents building, implementing, maintaining, or testing software.",
            "Present the specific supported projects, systems, and technologies; do not add a more specialized development claim unless it is documented.",
        )

    rules: tuple[
        tuple[set[str], str, re.Pattern[str], str, str, str], ...
    ] = (
        (
            _GENERIC_TECHNICAL_DOCUMENTATION_REQUIREMENTS,
            "technical_documentation",
            _TECHNICAL_DOCUMENTATION_RE,
            "Technical-documentation experience supported by documented engineering deliverables.",
            "The cited evidence shows creation or maintenance of documentation such as release documentation, API behavior, procedures, specifications, or technical test materials.",
            "Use the specific documentation type and audience shown in the evidence; do not broaden it to documentation you did not create.",
        ),
        (
            _GENERIC_PRODUCTION_TROUBLESHOOTING_REQUIREMENTS,
            "production_troubleshooting",
            _PRODUCTION_TROUBLESHOOTING_RE,
            "Production-support and troubleshooting capability supported by documented issue-resolution work.",
            "The cited evidence documents diagnosing, supporting, or resolving incidents, failures, defects, logs, or client-reported technical issues.",
            "Describe the verified issue, diagnostic approach, and result. Keep any production scope no broader than the cited evidence.",
        ),
        (
            _GENERIC_COLLABORATION_REQUIREMENTS,
            "collaboration",
            _COLLABORATION_RE,
            "Collaboration capability supported by documented cross-functional and stakeholder work.",
            "The cited evidence documents partnering, cross-functional delivery, stakeholder communication, or collaboration with clients and technical teams.",
            "Use one specific collaboration example and its outcome instead of a generic soft-skill claim.",
        ),
        (
            _GENERIC_REQUIREMENTS_TRANSLATION_REQUIREMENTS,
            "requirements_translation",
            _REQUIREMENTS_TRANSLATION_RE,
            "Requirements-analysis capability supported by documented translation of business or data needs into technical delivery.",
            "The cited evidence documents gathering, analyzing, or translating requirements into a solution, workflow, implementation, or tested software change.",
            "Present the verified requirement, the technical decision or implementation, and the resulting business outcome.",
        ),
        (
            _GENERIC_REUSABLE_DATA_COMPONENT_REQUIREMENTS,
            "reusable_data_components",
            _REUSABLE_DATA_COMPONENT_RE,
            "Reusable data-component experience supported by documented scalable or reusable implementation work.",
            "The cited evidence explicitly describes reusable, shared, configurable, or scalable data components, pipelines, procedures, frameworks, or workflows.",
            "Name the verified reusable component and scope. Do not infer scalability or reuse unless the evidence says so.",
        ),
        (
            _GENERIC_PERFORMANCE_OPTIMIZATION_REQUIREMENTS,
            "performance_optimization",
            _PERFORMANCE_OPTIMIZATION_RE,
            "Performance-optimization capability supported by documented tuning or diagnostic work.",
            "The cited evidence documents optimizing or diagnosing performance involving SQL, queries, databases, workflows, latency, throughput, or response time.",
            "Use the verified technique and outcome; retain metrics only when they are present in the evidence.",
        ),
        (
            _GENERIC_DATA_ARCHITECTURE_REQUIREMENTS,
            "data_architecture",
            _DATA_ARCHITECTURE_RE,
            "Data-architecture principles supported by documented data integration, ETL, modeling, database, or architecture work.",
            "The cited evidence shows design or implementation decisions across data sources, ETL workflows, database systems, or solution architecture.",
            "Use the specific architecture, integration, ETL, or database example shown in the evidence rather than a generic architecture claim.",
        ),
        (
            _GENERIC_PROCESS_AUTOMATION_REQUIREMENTS,
            "process_automation",
            _PROCESS_AUTOMATION_RE,
            "Process-improvement and automation capability supported by documented workflow, CI/CD, testing, or release automation.",
            "The cited evidence shows automation or streamlining that improved delivery, validation, deployment, testing, or operational efficiency.",
            "Name the verified process, automation, and result; do not imply ownership of automation beyond the cited work.",
        ),
        (
            _GENERIC_ANALYTICAL_PROBLEM_SOLVING_REQUIREMENTS,
            "analytical_problem_solving",
            _ANALYTICAL_PROBLEM_SOLVING_RE,
            "Analytical problem-solving capability supported by documented data analysis, transformation, validation, risk assessment, or issue resolution.",
            "The cited evidence demonstrates analysis of complex data, technical issues, controls, or requirements followed by a documented action or result.",
            "Show the specific problem, analysis or diagnostic method, and verified result instead of adding a generic soft-skill statement.",
        ),
        (
            _GENERIC_RESILIENCY_TUNING_REQUIREMENTS,
            "resiliency_tuning",
            _RESILIENCY_TUNING_RE,
            "Platform resiliency or performance-tuning capability supported by documented optimization, architecture, stability, or issue-resolution work.",
            "The cited evidence shows SQL or workflow optimization, architecture redesign, improved operational stability, or system reliability work.",
            "Use the verified tuning, stability, or architecture example and preserve the exact scope and result.",
        ),
    )

    for aliases, skill_key, pattern, meaning, rationale, action in rules:
        if core not in aliases:
            continue
        evidence_ids = _pattern_capability_evidence_ids(
            profile,
            skill_key=skill_key,
            evidence_pattern=pattern,
        )
        # A profile-level skill alone is useful context, but broad responsibility
        # wording should be upgraded only when at least one concrete role or
        # supplemental-evidence record demonstrates it.
        if any(evidence_id != "CANDIDATE-PROFILE" for evidence_id in evidence_ids):
            return evidence_ids, meaning, rationale, action
        return None
    return None


def _profile_contains(profile: CandidateProfile, value: str) -> bool:
    return bool(value.strip()) and _normalized(value) in _normalized(profile.all_source_text())


def _education_evidence_ids(profile: CandidateProfile, value: str) -> list[str]:
    """Return education evidence IDs for exact or safely abbreviated credential wording.

    Credential findings often use a familiar institution name such as "UC Berkeley"
    while the imported resume preserves the official name "University of California,
    Berkeley". Exact substring matching incorrectly treated those records as missing.
    Require strong token coverage so abbreviations can match without linking unrelated
    education entries.
    """

    normalized = _normalized(value)
    value_tokens = _tokens(value)
    matches: list[str] = []
    for index, education in enumerate(profile.education, start=1):
        education_text = " ".join(
            value
            for value in (
                education.credential,
                education.institution,
                education.location,
                education.date,
                education.detail,
            )
            if value.strip()
        )
        normalized_education = _normalized(education_text)
        exact_match = bool(normalized) and (
            normalized in normalized_education or normalized_education in normalized
        )
        education_tokens = _tokens(education_text)
        overlap = value_tokens & education_tokens
        token_match = bool(value_tokens) and (
            len(overlap) >= 3
            and len(overlap) / len(value_tokens) >= 0.7
        )
        if exact_match or token_match:
            matches.append(f"EDUCATION-{index}")
    return matches


def _evidence_text_lookup(profile: CandidateProfile) -> dict[str, str]:
    lookup: dict[str, str] = {"CANDIDATE-PROFILE": profile.all_source_text()}
    for experience in profile.experiences:
        lookup[experience.id] = " ".join(
            value
            for value in (
                experience.title,
                experience.employer,
                experience.location,
                experience.dates,
                *(bullet.text for bullet in experience.bullets),
            )
            if value.strip()
        )
        for bullet in experience.bullets:
            lookup[bullet.id] = bullet.text
    for index, education in enumerate(profile.education, start=1):
        lookup[f"EDUCATION-{index}"] = " ".join(
            value
            for value in (
                education.credential,
                education.institution,
                education.location,
                education.date,
                education.detail,
            )
            if value.strip()
        )
    for item in profile.supplemental_evidence:
        lookup[item.id] = " ".join(
            value for value in (item.statement, *item.verified_skills) if value.strip()
        )
    return lookup


def _valid_evidence_ids(profile: CandidateProfile) -> set[str]:
    ids = {experience.id for experience in profile.experiences}
    ids.update(profile.bullet_lookup())
    ids.update(item.id for item in profile.supplemental_evidence)
    ids.update(f"EDUCATION-{index}" for index, _ in enumerate(profile.education, start=1))
    ids.add("CANDIDATE-PROFILE")
    return ids


def _evidence_ids_for_text(profile: CandidateProfile, value: str) -> list[str]:
    normalized = _normalized(value)
    if not normalized:
        return []
    matches: list[str] = []
    for experience in profile.experiences:
        if normalized in _normalized(experience.title):
            matches.append(experience.id)
        for bullet in experience.bullets:
            if normalized in _normalized(bullet.text):
                matches.append(bullet.id)
    matches.extend(_education_evidence_ids(profile, value))
    for item in profile.supplemental_evidence:
        if normalized in _normalized(item.statement):
            matches.append(item.id)
    if not matches and normalized in _normalized(profile.all_source_text()):
        matches.append("CANDIDATE-PROFILE")
    return list(dict.fromkeys(matches))


def _source_text_is_verbatim_evidence(
    source_text: str,
    evidence_texts: list[str],
) -> bool:
    """Return whether an official term is copied verbatim from cited evidence.

    Product, platform, technology, acronym, and other proper names must often be
    preserved rather than translated.  A model-generated explanation can fail
    grounding even though the official source term itself is fully verified.  In
    that case the safe outcome is to keep the verified name and discard the
    unsupported explanation, not to ask the candidate to reconfirm the name.
    """

    normalized_source = _normalized(source_text)
    return bool(normalized_source) and any(
        normalized_source in _normalized(evidence_text)
        for evidence_text in evidence_texts
    )


def _preserve_verified_official_terminology(
    finding: CareerTranslationFinding,
) -> None:
    """Keep a verified official name while removing an unsupported explanation."""

    finding.disposition = "confirmed_experience"
    finding.translated_meaning = "Official name preserved exactly as written."
    finding.rationale = (
        "The official terminology is directly traceable to Verified Resume Evidence. "
        "An unverified generated explanation was removed."
    )
    finding.recommended_action = (
        "Keep the official name unchanged. Add a target-market explanation only when "
        "that explanation is independently supported by verified evidence."
    )


_REQUIREMENT_FINDING_CATEGORIES = {
    "unsupported_requirement",
    "missing_evidence",
    "transferable_skill",
}
_REQUIREMENT_DISPOSITION_PRIORITY = {
    "confirmed_experience": 5,
    "reasonable_rephrasing": 4,
    "user_clarification_required": 3,
    "unsupported_claim": 2,
    "recommended_learning_or_future_action": 1,
}


def _is_explicitly_confirmed_development_gap(
    finding: CareerTranslationFinding,
) -> bool:
    """Return whether wording records an explicit candidate-confirmed gap."""

    text = _normalized(
        " ".join(
            (
                finding.translated_meaning,
                finding.rationale,
                finding.recommended_action,
            )
        )
    )
    return any(
        marker in text
        for marker in (
            "candidate confirmed",
            "candidate has confirmed",
            "candidate indicated they do not",
            "candidate does not have this experience",
            "confirmed development gap",
            "negative answer confirmed",
        )
    )


def _consolidate_requirement_findings(
    findings: list[CareerTranslationFinding],
) -> list[CareerTranslationFinding]:
    """Show one coherent Target-Market Review card per requirement.

    Model output can contain both an unsupported-requirement card and a
    missing-evidence card for the same wording. The candidate then sees the same
    requirement twice with competing instructions. Consolidate exact requirement
    wording while preserving the strongest safe state and all traceable evidence.
    """

    grouped: dict[str, list[tuple[int, CareerTranslationFinding]]] = {}
    passthrough: list[tuple[int, CareerTranslationFinding]] = []
    for index, finding in enumerate(findings):
        if finding.category not in _REQUIREMENT_FINDING_CATEGORIES:
            passthrough.append((index, finding))
            continue
        key = _normalized(finding.source_text)
        if not key:
            passthrough.append((index, finding))
            continue
        grouped.setdefault(key, []).append((index, finding))

    consolidated: list[tuple[int, CareerTranslationFinding]] = list(passthrough)
    for rows in grouped.values():
        first_index = min(index for index, _ in rows)
        if len(rows) == 1:
            single = rows[0][1].model_copy(deep=True)
            if (
                single.disposition == "recommended_learning_or_future_action"
                and not _is_explicitly_confirmed_development_gap(single)
            ):
                single.disposition = "unsupported_claim"
                single.category = "unsupported_requirement"
                single.translated_meaning = (
                    "Current evidence does not yet support using this requirement in the resume."
                )
                single.rationale = (
                    "No Verified Resume Evidence is linked to this requirement. Absence from the resume is not proof that the candidate lacks the experience."
                )
                single.recommended_action = (
                    "Keep it outside the resume for now. Add a verified example later if applicable; otherwise no action is required."
                )
            consolidated.append((first_index, single))
            continue

        def rank(row: tuple[int, CareerTranslationFinding]) -> tuple[int, int, int]:
            _, finding = row
            return (
                _REQUIREMENT_DISPOSITION_PRIORITY[finding.disposition],
                1 if finding.category == "missing_evidence" else 0,
                len(finding.evidence_ids),
            )

        winner = max(rows, key=rank)[1].model_copy(deep=True)
        winner.evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for _, finding in rows
                for evidence_id in finding.evidence_ids
            )
        )

        if winner.disposition == "confirmed_experience":
            winner.category = "transferable_skill"
        elif winner.disposition == "user_clarification_required":
            question_finding = next(
                (
                    finding
                    for _, finding in rows
                    if finding.category == "missing_evidence"
                    and finding.disposition == "user_clarification_required"
                ),
                None,
            )
            if question_finding is not None:
                winner = question_finding.model_copy(deep=True)
                winner.evidence_ids = list(
                    dict.fromkeys(
                        evidence_id
                        for _, finding in rows
                        for evidence_id in finding.evidence_ids
                    )
                )
                winner.category = "missing_evidence"
        elif (
            winner.disposition == "recommended_learning_or_future_action"
            and not _is_explicitly_confirmed_development_gap(winner)
        ):
            # A missing phrase is not proof of a confirmed development gap. Until
            # the candidate explicitly says they lack the experience, keep the
            # requirement outside the resume without prescribing learning.
            winner.disposition = "unsupported_claim"
            winner.category = "unsupported_requirement"
            winner.translated_meaning = (
                "Current evidence does not yet support using this requirement in the resume."
            )
            winner.rationale = (
                "No Verified Resume Evidence is linked to this requirement. Absence from the resume is not proof that the candidate lacks the experience."
            )
            winner.recommended_action = (
                "Keep it outside the resume for now. Add a verified example later if applicable; otherwise no action is required."
            )

        consolidated.append((first_index, winner))

    consolidated.sort(key=lambda row: row[0])
    return [finding for _, finding in consolidated]


def ensure_career_translation_assessment(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    background: NewcomerCareerProfile | None = None,
) -> TailoringProposal:
    """Supplement the model assessment with deterministic evidence-protection checks.

    The model remains responsible for nuanced terminology and title translation. This
    function guarantees that user-entered credentials are never silently treated as
    evidence, unsupported job requirements stay visible, and confirmation questions
    are represented as missing-evidence findings.
    """

    context = background or NewcomerCareerProfile()
    updated = proposal.model_copy(deep=True)
    assessment = updated.career_translation_assessment.model_copy(deep=True)
    assessment.target_country = assessment.target_country or context.target_country
    assessment.target_role = assessment.target_role or context.target_role or analysis.target_title

    valid_ids = _valid_evidence_ids(profile)
    evidence_lookup = _evidence_text_lookup(profile)
    requirement_lookup = {item.id: item for item in analysis.requirements}

    broad_capability_evidence: dict[
        str,
        tuple[list[str], str, str, str],
    ] = {}
    for match in updated.evidence_matches:
        requirement = requirement_lookup.get(match.requirement_id)
        if requirement is None or match.status != "unsupported":
            continue
        capability_support = _broad_capability_support(
            profile,
            requirement.requirement,
        )
        if capability_support is None:
            continue
        evidence_ids, meaning, rationale, action = capability_support
        match.status = "supported"
        match.evidence_ids = evidence_ids
        match.rationale = rationale
        broad_capability_evidence[requirement.id] = (
            evidence_ids,
            meaning,
            rationale,
            action,
        )

    if broad_capability_evidence:
        supported_requirement_text = {
            _normalized(requirement_lookup[requirement_id].requirement)
            for requirement_id in broad_capability_evidence
        }
        updated.unsupported_requirements = [
            item
            for item in updated.unsupported_requirements
            if _normalized(item) not in supported_requirement_text
        ]
        updated.candidate_questions = [
            item
            for item in updated.candidate_questions
            if item.requirement_id not in broad_capability_evidence
        ]

    job_context = "\n".join(
        [
            analysis.target_title,
            analysis.target_company,
            *(requirement.requirement for requirement in analysis.requirements),
            *(keyword for requirement in analysis.requirements for keyword in requirement.keywords),
        ]
    )
    protected_findings: list[CareerTranslationFinding] = []
    for finding in assessment.findings:
        protected = finding.model_copy(deep=True)
        protected.evidence_ids = [
            evidence_id
            for evidence_id in dict.fromkeys(protected.evidence_ids)
            if evidence_id in valid_ids
        ]
        supported_requirement = next(
            (
                requirement_lookup[requirement_id]
                for requirement_id in broad_capability_evidence
                if _normalized(protected.source_text)
                == _normalized(requirement_lookup[requirement_id].requirement)
            ),
            None,
        )
        if (
            supported_requirement is not None
            and protected.category in {"unsupported_requirement", "missing_evidence"}
        ):
            evidence_ids, meaning, rationale, action = broad_capability_evidence[
                supported_requirement.id
            ]
            protected.category = "transferable_skill"
            protected.disposition = "confirmed_experience"
            protected.evidence_ids = evidence_ids
            protected.translated_meaning = meaning
            protected.rationale = rationale
            protected.recommended_action = action
            protected_findings.append(protected)
            continue

        legacy_untraceable_terminology = (
            protected.category == "regional_terminology"
            and protected.disposition == "user_clarification_required"
            and (
                protected.rationale.strip() == _UNTRACEABLE_INTERPRETATION_MESSAGE
                or protected.translated_meaning.strip() == _CANDIDATE_CONFIRMATION_MEANING
            )
        )
        if legacy_untraceable_terminology:
            recovered_evidence_ids = protected.evidence_ids or _evidence_ids_for_text(
                profile,
                protected.source_text,
            )
            recovered_evidence = [
                evidence_lookup[evidence_id]
                for evidence_id in recovered_evidence_ids
                if evidence_id in evidence_lookup
            ]
            if _source_text_is_verbatim_evidence(
                protected.source_text,
                recovered_evidence,
            ):
                protected.evidence_ids = recovered_evidence_ids
                _preserve_verified_official_terminology(protected)

        if protected.disposition in {
            "confirmed_experience",
            "reasonable_rephrasing",
        } and not protected.evidence_ids:
            protected.evidence_ids = _evidence_ids_for_text(
                profile, protected.source_text
            )
            if not protected.evidence_ids:
                protected.disposition = "user_clarification_required"
                protected.rationale = (
                    protected.rationale.rstrip()
                    + " The assessment could not trace this item to Verified Resume Evidence, so it cannot shape resume wording yet."
                ).strip()
                protected.recommended_action = protected.recommended_action or (
                    "Confirm the official credential name, issuing institution, and completion date, "
                    "then link it to verified education or certification evidence before using it."
                    if protected.category == "credential_explanation"
                    else "Confirm the exact fact and attach it to a documented role before using it."
                )

        if protected.disposition in {"confirmed_experience", "reasonable_rephrasing"}:
            cited_evidence = [
                evidence_lookup[evidence_id]
                for evidence_id in protected.evidence_ids
                if evidence_id in evidence_lookup
            ]
            verified_official_terminology = (
                protected.category == "regional_terminology"
                and _source_text_is_verbatim_evidence(
                    protected.source_text,
                    cited_evidence,
                )
            )
            source_findings = validate_candidate_claim(
                protected.source_text,
                cited_evidence,
                require_overlap=True,
            ) if cited_evidence else []
            if protected.category == "credential_explanation":
                matched_education_ids = set(
                    _education_evidence_ids(profile, protected.source_text)
                )
                if matched_education_ids & set(protected.evidence_ids):
                    # The displayed source may use a familiar institution alias such
                    # as "UC Berkeley" while the evidence preserves the official
                    # institution name. Strong credential-token coverage already
                    # established that these refer to the same education record.
                    source_findings = []
            translation_findings = validate_candidate_claim(
                protected.translated_meaning,
                cited_evidence,
                context_texts=[job_context],
                allow_gap_context=False,
                require_overlap=True,
            ) if cited_evidence and protected.translated_meaning.strip() else []
            if (
                verified_official_terminology
                and not source_findings
                and translation_findings
            ):
                # The official term is verified, but the generated explanation is
                # not. Preserve the exact product/platform/professional name and
                # remove only the unsupported interpretation. Reconfirmation is
                # unnecessary because the source wording is already traceable.
                _preserve_verified_official_terminology(protected)
            elif not cited_evidence or source_findings or translation_findings:
                protected.disposition = "user_clarification_required"
                protected.evidence_ids = []
                protected.translated_meaning = _CANDIDATE_CONFIRMATION_MEANING
                protected.rationale = _UNTRACEABLE_INTERPRETATION_MESSAGE
                protected.recommended_action = (
                    "Confirm the official wording, factual responsibilities, and closest target-market "
                    "explanation before using it."
                )
            else:
                protected.rationale = (
                    "The credential is linked to verified education evidence; any explanation must preserve its official facts."
                    if protected.category == "credential_explanation"
                    else "The source wording and evidence IDs are present in Verified Resume Evidence; any explanation must preserve those facts."
                )
                protected.recommended_action = (
                    "Preserve the official credential and institution names; add completion or equivalency context only when verified."
                    if protected.category == "credential_explanation"
                    else "Keep official facts unchanged and use only the evidence-supported explanation."
                )
        protected_findings.append(protected)
    assessment.findings = protected_findings

    existing_index = {
        _finding_key(item): index
        for index, item in enumerate(assessment.findings)
    }

    def add(
        finding: CareerTranslationFinding,
        *,
        authoritative: bool = False,
    ) -> None:
        key = _finding_key(finding)
        index = existing_index.get(key)
        if index is not None:
            if authoritative:
                current = assessment.findings[index]
                replacement = finding.model_copy(deep=True)
                if (
                    current.translated_meaning.strip()
                    and finding.category
                    not in {
                        "unsupported_requirement",
                        "missing_evidence",
                        "transferable_skill",
                    }
                ):
                    replacement.translated_meaning = current.translated_meaning
                assessment.findings[index] = replacement
            return
        assessment.findings.append(finding)
        existing_index[key] = len(assessment.findings) - 1

    title_lookup = {
        _normalized(experience.title): experience
        for experience in profile.experiences
        if experience.title.strip()
    }
    for title in context.unfamiliar_job_titles:
        matched = title_lookup.get(_normalized(title))
        add(
            CareerTranslationFinding(
                category="job_title_translation",
                source_text=title,
                translated_meaning=(
                    f"Explain this documented title in recruiter-readable terms for {assessment.target_role}."
                    if matched
                    else "The title needs a factual description of responsibilities before it can be translated safely."
                ),
                disposition=(
                    "reasonable_rephrasing" if matched else "user_clarification_required"
                ),
                evidence_ids=[matched.id] if matched else [],
                rationale=(
                    "The exact title is documented in the Verified Resume Evidence; only its explanation may be clarified."
                    if matched
                    else "The title was supplied as onboarding context but is not independently documented in the Verified Resume Evidence."
                ),
                recommended_action=(
                    "Keep the official title unchanged and add a concise functional explanation only when useful."
                    if matched
                    else "Confirm the employer, dates, responsibilities, and closest target-market equivalent before using it."
                ),
            ),
            authoritative=True,
        )

    credential_items = [
        ("International credential", item)
        for item in context.international_credentials
    ] + [
        ("Professional certification", item)
        for item in context.professional_certifications
    ]
    for kind, credential in credential_items:
        evidence_ids = _education_evidence_ids(profile, credential)
        documented = bool(evidence_ids) or _profile_contains(profile, credential)
        if documented and not evidence_ids:
            evidence_ids.append("CANDIDATE-PROFILE")
        add(
            CareerTranslationFinding(
                category="credential_explanation",
                source_text=credential,
                translated_meaning=(
                    f"{kind} that may need a short target-market explanation."
                ),
                disposition=(
                    "confirmed_experience" if documented else "user_clarification_required"
                ),
                evidence_ids=evidence_ids,
                rationale=(
                    "The credential is present in the Verified Resume Evidence and may be explained without changing its official name."
                    if documented
                    else "The credential was entered during onboarding but is not yet documented in the Verified Resume Evidence."
                ),
                recommended_action=(
                    "Preserve the official credential name and add issuing body or equivalency context only when verified."
                    if documented
                    else "Confirm the official name, issuer, country, completion status, and any verified equivalency before adding it."
                ),
            ),
            authoritative=True,
        )

    question_requirements = {
        item.requirement_id for item in updated.candidate_questions if item.requirement_id
    }
    unsupported_ids = {
        match.requirement_id
        for match in updated.evidence_matches
        if match.status == "unsupported"
    }
    for requirement_id in unsupported_ids:
        requirement = requirement_lookup.get(requirement_id)
        if requirement is None:
            continue
        needs_question = requirement_id in question_requirements
        if needs_question:
            # The missing-evidence finding below is the single visible card for
            # this requirement. Do not also add an unsupported-requirement card.
            continue
        confirmed_gap_finding = next(
            (
                finding
                for finding in assessment.findings
                if finding.category == "unsupported_requirement"
                and _normalized(finding.source_text)
                == _normalized(requirement.requirement)
                and finding.disposition
                == "recommended_learning_or_future_action"
                and _is_explicitly_confirmed_development_gap(finding)
            ),
            None,
        )
        if confirmed_gap_finding is not None:
            # A post-confirmation proposal may retain a development suggestion
            # only when its wording records the candidate's explicit negative
            # answer. Missing evidence alone never creates this state.
            continue
        add(
            CareerTranslationFinding(
                category="unsupported_requirement",
                source_text=requirement.requirement,
                translated_meaning=(
                    "Current evidence does not yet support using this requirement in the resume."
                ),
                disposition="unsupported_claim",
                evidence_ids=[],
                rationale=(
                    "No Verified Resume Evidence is linked to this requirement. Absence from the resume is not proof that the candidate lacks the experience."
                ),
                recommended_action=(
                    "Keep it outside the resume for now. Add a verified example later if applicable; otherwise no action is required."
                ),
            ),
            authoritative=True,
        )

    for question in updated.candidate_questions:
        requirement = requirement_lookup.get(question.requirement_id)
        add(
            CareerTranslationFinding(
                category="missing_evidence",
                source_text=(
                    requirement.requirement if requirement is not None else question.question
                ),
                translated_meaning=(
                    "Potentially relevant experience needs one factual confirmation before it can be used."
                ),
                disposition="user_clarification_required",
                evidence_ids=(
                    [question.source_id]
                    if question.source_id in valid_ids
                    else []
                ),
                rationale=question.help_text or question.question,
                recommended_action=(
                    question.details_prompt
                    or "Answer the related question below with one concise factual example, or select No to keep the requirement outside the resume."
                ),
            ),
            authoritative=True,
        )

    requirement_tokens = {
        item.id: _tokens(" ".join([item.requirement, *item.keywords]))
        for item in analysis.requirements
    }
    supported_or_partial_requirement_ids = {
        match.requirement_id
        for match in updated.evidence_matches
        if match.status != "unsupported"
    }
    transfer_candidates: list[tuple[int, str, str, list[str]]] = []
    for experience in profile.experiences:
        for bullet in experience.bullets:
            bullet_tokens = _tokens(bullet.text)
            matched_ids = [
                requirement_id
                for requirement_id, tokens in requirement_tokens.items()
                if requirement_id in supported_or_partial_requirement_ids
                and len(bullet_tokens & tokens) >= 2
            ]
            if matched_ids:
                transfer_candidates.append(
                    (len(matched_ids), bullet.id, bullet.text, matched_ids)
                )
    transfer_candidates.sort(key=lambda row: (-row[0], row[1]))
    for _, bullet_id, bullet_text, matched_ids in transfer_candidates[:3]:
        labels = [requirement_lookup[item].requirement for item in matched_ids[:3]]
        add(
            CareerTranslationFinding(
                category="transferable_skill",
                source_text=bullet_text,
                translated_meaning="Transferable evidence for: " + "; ".join(labels),
                disposition="confirmed_experience",
                evidence_ids=[bullet_id],
                rationale="This documented accomplishment overlaps with multiple target-role requirements even when the original industry or title differs.",
                recommended_action="Lead with the supported responsibility or outcome and keep the original employer, title, scope, and metrics unchanged.",
            )
        )

    assessment.findings = _consolidate_requirement_findings(assessment.findings)

    counts = Counter(item.disposition for item in assessment.findings)
    base_summary = (
        "Career Bridge reviewed documented titles, credentials, terminology, accomplishments, "
        "and transferable skills while keeping unsupported interpretations outside candidate claims."
    )
    assessment.summary = (
        base_summary
        + " Review result: "
        + f"{counts['confirmed_experience']} confirmed item(s), "
        + f"{counts['reasonable_rephrasing']} safe rephrasing(s), "
        + f"{counts['user_clarification_required']} evidence question(s), "
        + f"{counts['unsupported_claim']} requirement(s) kept outside the resume, and "
        + f"{counts['recommended_learning_or_future_action']} confirmed development opportunity item(s)."
    )

    updated.career_translation_assessment = CareerTranslationAssessment.model_validate(
        assessment.model_dump()
    )
    return updated
