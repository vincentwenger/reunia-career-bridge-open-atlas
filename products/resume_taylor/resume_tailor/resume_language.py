from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .models import CandidateProfile


@dataclass(frozen=True)
class ResumeLanguageChoice:
    code: str
    name: str
    source: str
    country: str = ""


SUPPORTED_RESUME_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
)
_LANGUAGE_NAME_TO_CODE = {name.casefold(): code for code, name in SUPPORTED_RESUME_LANGUAGES}
_LANGUAGE_CODE_TO_NAME = dict(SUPPORTED_RESUME_LANGUAGES)

# The first language is the normal default. Countries with more than one listed
# language use the job-description language when it can be detected reliably.
_COUNTRY_LANGUAGES: dict[str, tuple[str, ...]] = {
    "united states": ("en",),
    "united states of america": ("en",),
    "usa": ("en",),
    "us": ("en",),
    "u.s.": ("en",),
    "united kingdom": ("en",),
    "uk": ("en",),
    "england": ("en",),
    "scotland": ("en",),
    "wales": ("en",),
    "ireland": ("en",),
    "australia": ("en",),
    "new zealand": ("en",),
    "singapore": ("en",),
    "india": ("en",),
    "france": ("fr",),
    "monaco": ("fr",),
    "germany": ("de",),
    "austria": ("de",),
    "spain": ("es",),
    "mexico": ("es",),
    "argentina": ("es",),
    "colombia": ("es",),
    "chile": ("es",),
    "peru": ("es",),
    "italy": ("it",),
    "portugal": ("pt",),
    "brazil": ("pt",),
    "netherlands": ("nl",),
    "canada": ("en", "fr"),
    "belgium": ("nl", "fr", "de"),
    "switzerland": ("de", "fr", "it"),
    "luxembourg": ("fr", "de"),
}

_LANGUAGE_MARKERS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the and with for from to of in on at by as a an this that these those experience skills professional managed developed implemented supported delivered worked using including across within".split()
    ),
    "fr": frozenset(
        "le la les un une des du de et avec pour dans sur par comme ce cette ces expérience compétences professionnel professionnelle géré développé développé mise oeuvre travaillé utilisant notamment auprès entre".split()
    ),
    "de": frozenset(
        "der die das den dem ein eine und mit für von zu im in auf bei als diese dieser erfahrung kenntnisse beruflich geleitet entwickelt umgesetzt gearbeitet unter verwendung einschließlich".split()
    ),
    "es": frozenset(
        "el la los las un una de del y con para desde en sobre por como este esta experiencia habilidades profesional gestionó desarrolló implementó trabajó utilizando incluyendo".split()
    ),
    "it": frozenset(
        "il lo la i gli le un una di del e con per da in su presso come questo questa esperienza competenze professionale gestito sviluppato implementato lavorato utilizzando incluso".split()
    ),
    "pt": frozenset(
        "o a os as um uma de do da e com para desde em sobre por como este esta experiência competências profissional gerenciou desenvolveu implementou trabalhou utilizando incluindo".split()
    ),
    "nl": frozenset(
        "de het een en met voor van tot in op bij als deze dit ervaring vaardigheden professioneel beheerde ontwikkelde implementeerde werkte gebruikmakend inclusief".split()
    ),
}
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", re.UNICODE)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")

_DATE_MONTHS: dict[str, str] = {
    # English
    "january": "01",
    "jan": "01",
    "february": "02",
    "feb": "02",
    "march": "03",
    "mar": "03",
    "april": "04",
    "apr": "04",
    "may": "05",
    "june": "06",
    "jun": "06",
    "july": "07",
    "jul": "07",
    "august": "08",
    "aug": "08",
    "september": "09",
    "sep": "09",
    "sept": "09",
    "october": "10",
    "oct": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
    # French
    "janvier": "01",
    "janv": "01",
    "février": "02",
    "fevrier": "02",
    "févr": "02",
    "fevr": "02",
    "mars": "03",
    "avril": "04",
    "avr": "04",
    "mai": "05",
    "juin": "06",
    "juillet": "07",
    "juil": "07",
    "août": "08",
    "aout": "08",
    "septembre": "09",
    "octobre": "10",
    "novembre": "11",
    "décembre": "12",
    "decembre": "12",
    "déc": "12",
    # German
    "januar": "01",
    "februar": "02",
    "märz": "03",
    "maerz": "03",
    "juni": "06",
    "juli": "07",
    "oktober": "10",
    "dezember": "12",
    # Spanish / Italian / Portuguese / Dutch common forms
    "enero": "01",
    "gennaio": "01",
    "janeiro": "01",
    "febrero": "02",
    "febbraio": "02",
    "fevereiro": "02",
    "marzo": "03",
    "março": "03",
    "marco": "03",
    "abril": "04",
    "aprile": "04",
    "mayo": "05",
    "maggio": "05",
    "junio": "06",
    "giugno": "06",
    "junho": "06",
    "julio": "07",
    "luglio": "07",
    "julho": "07",
    "agosto": "08",
    "septiembre": "09",
    "settembre": "09",
    "setembro": "09",
    "octubre": "10",
    "ottobre": "10",
    "outubro": "10",
    "noviembre": "11",
    "novembro": "11",
    "diciembre": "12",
    "dicembre": "12",
    "dezembro": "12",
    "januari": "01",
    "februari": "02",
    "maart": "03",
    "mei": "05",
    "augustus": "08",
}

_DATE_PRESENT_WORDS = frozenset(
    {
        "present",
        "current",
        "currently",
        "présent",
        "actuel",
        "actuelle",
        "aujourd'hui",
        "aujourdhui",
        "heute",
        "aktuell",
        "actualidad",
        "actualmente",
        "presente",
        "atual",
        "atualmente",
        "heden",
        "huidig",
    }
)

_RESUME_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "professional_summary": "Professional Summary",
        "technical_profile": "Technical Profile",
        "professional_profile": "Professional Profile",
        "skills": "Skills",
        "technical_skills": "Technical Skills",
        "transferable_skills": "Transferable and Relevant Skills",
        "core_capabilities": "Core Capabilities",
        "experience": "Work Experience",
        "engineering_experience": "Engineering Experience",
        "relevant_experience": "Relevant Experience",
        "client_experience": "Client and Project Experience",
        "education": "Education and Professional Development",
        "education_certifications": "Education and Certifications",
        "education_credentials": "Education and Credentials",
        "hard_skills": "Hard Skills",
        "soft_skills": "Soft Skills",
        "tools_software": "Tools & Software",
        "industry_knowledge": "Industry Knowledge",
        "languages": "Languages",
        "job_title": "Job Title",
    },
    "fr": {
        "professional_summary": "Profil professionnel",
        "technical_profile": "Profil technique",
        "professional_profile": "Profil professionnel",
        "skills": "Compétences",
        "technical_skills": "Compétences techniques",
        "transferable_skills": "Compétences transférables et pertinentes",
        "core_capabilities": "Compétences clés",
        "experience": "Expérience professionnelle",
        "engineering_experience": "Expérience en ingénierie",
        "relevant_experience": "Expérience pertinente",
        "client_experience": "Expérience clients et projets",
        "education": "Formation et développement professionnel",
        "education_certifications": "Formation et certifications",
        "education_credentials": "Formation et diplômes",
        "hard_skills": "Compétences spécialisées",
        "soft_skills": "Compétences relationnelles",
        "tools_software": "Outils et logiciels",
        "industry_knowledge": "Connaissance du secteur",
        "languages": "Langues",
        "job_title": "Intitulé du poste",
    },
    "de": {
        "professional_summary": "Berufliches Profil",
        "technical_profile": "Technisches Profil",
        "professional_profile": "Berufliches Profil",
        "skills": "Kompetenzen",
        "technical_skills": "Technische Kompetenzen",
        "transferable_skills": "Übertragbare und relevante Kompetenzen",
        "core_capabilities": "Kernkompetenzen",
        "experience": "Berufserfahrung",
        "engineering_experience": "Engineering-Erfahrung",
        "relevant_experience": "Relevante Erfahrung",
        "client_experience": "Kunden- und Projekterfahrung",
        "education": "Ausbildung und berufliche Weiterbildung",
        "education_certifications": "Ausbildung und Zertifizierungen",
        "education_credentials": "Ausbildung und Abschlüsse",
        "hard_skills": "Fachliche Kompetenzen",
        "soft_skills": "Soziale Kompetenzen",
        "tools_software": "Tools und Software",
        "industry_knowledge": "Branchenkenntnisse",
        "languages": "Sprachen",
        "job_title": "Berufsbezeichnung",
    },
    "es": {
        "professional_summary": "Perfil profesional",
        "technical_profile": "Perfil técnico",
        "professional_profile": "Perfil profesional",
        "skills": "Competencias",
        "technical_skills": "Competencias técnicas",
        "transferable_skills": "Competencias transferibles y relevantes",
        "core_capabilities": "Capacidades clave",
        "experience": "Experiencia profesional",
        "engineering_experience": "Experiencia en ingeniería",
        "relevant_experience": "Experiencia relevante",
        "client_experience": "Experiencia con clientes y proyectos",
        "education": "Formación y desarrollo profesional",
        "education_certifications": "Formación y certificaciones",
        "education_credentials": "Formación y credenciales",
        "hard_skills": "Competencias especializadas",
        "soft_skills": "Competencias interpersonales",
        "tools_software": "Herramientas y software",
        "industry_knowledge": "Conocimiento del sector",
        "languages": "Idiomas",
        "job_title": "Puesto objetivo",
    },
    "it": {
        "professional_summary": "Profilo professionale",
        "technical_profile": "Profilo tecnico",
        "professional_profile": "Profilo professionale",
        "skills": "Competenze",
        "technical_skills": "Competenze tecniche",
        "transferable_skills": "Competenze trasferibili e pertinenti",
        "core_capabilities": "Competenze chiave",
        "experience": "Esperienza professionale",
        "engineering_experience": "Esperienza ingegneristica",
        "relevant_experience": "Esperienza pertinente",
        "client_experience": "Esperienza clienti e progetti",
        "education": "Formazione e sviluppo professionale",
        "education_certifications": "Formazione e certificazioni",
        "education_credentials": "Formazione e qualifiche",
        "hard_skills": "Competenze specialistiche",
        "soft_skills": "Competenze relazionali",
        "tools_software": "Strumenti e software",
        "industry_knowledge": "Conoscenza del settore",
        "languages": "Lingue",
        "job_title": "Titolo professionale",
    },
    "pt": {
        "professional_summary": "Perfil profissional",
        "technical_profile": "Perfil técnico",
        "professional_profile": "Perfil profissional",
        "skills": "Competências",
        "technical_skills": "Competências técnicas",
        "transferable_skills": "Competências transferíveis e relevantes",
        "core_capabilities": "Competências principais",
        "experience": "Experiência profissional",
        "engineering_experience": "Experiência em engenharia",
        "relevant_experience": "Experiência relevante",
        "client_experience": "Experiência com clientes e projetos",
        "education": "Formação e desenvolvimento profissional",
        "education_certifications": "Formação e certificações",
        "education_credentials": "Formação e qualificações",
        "hard_skills": "Competências especializadas",
        "soft_skills": "Competências interpessoais",
        "tools_software": "Ferramentas e software",
        "industry_knowledge": "Conhecimento do setor",
        "languages": "Idiomas",
        "job_title": "Cargo profissional",
    },
    "nl": {
        "professional_summary": "Professioneel profiel",
        "technical_profile": "Technisch profiel",
        "professional_profile": "Professioneel profiel",
        "skills": "Vaardigheden",
        "technical_skills": "Technische vaardigheden",
        "transferable_skills": "Overdraagbare en relevante vaardigheden",
        "core_capabilities": "Kernvaardigheden",
        "experience": "Werkervaring",
        "engineering_experience": "Engineeringervaring",
        "relevant_experience": "Relevante ervaring",
        "client_experience": "Klant- en projectervaring",
        "education": "Opleiding en professionele ontwikkeling",
        "education_certifications": "Opleiding en certificeringen",
        "education_credentials": "Opleiding en kwalificaties",
        "hard_skills": "Vakinhoudelijke vaardigheden",
        "soft_skills": "Persoonlijke vaardigheden",
        "tools_software": "Tools en software",
        "industry_knowledge": "Branchekennis",
        "languages": "Talen",
        "job_title": "Functietitel",
    },
}


def normalize_language(value: str) -> str:
    raw = " ".join(str(value or "").split()).casefold()
    if not raw:
        return ""
    if raw in _LANGUAGE_CODE_TO_NAME:
        return raw
    return _LANGUAGE_NAME_TO_CODE.get(raw, "")


def language_name(code_or_name: str) -> str:
    code = normalize_language(code_or_name) or "en"
    return _LANGUAGE_CODE_TO_NAME.get(code, "English")


def detect_text_language(text: str, allowed: tuple[str, ...] | None = None) -> str:
    tokens = [token.casefold() for token in _WORD_RE.findall(text or "")]
    if not tokens:
        return ""
    candidates = allowed or tuple(_LANGUAGE_MARKERS)
    scores = {
        code: sum(1 for token in tokens if token in _LANGUAGE_MARKERS.get(code, ()))
        for code in candidates
    }
    best = max(scores, key=scores.get, default="")
    if not best or scores[best] < 3:
        return ""
    ordered = sorted(scores.values(), reverse=True)
    if len(ordered) > 1 and ordered[0] == ordered[1]:
        return ""
    return best


def resolve_resume_language(
    target_country: str,
    explicit_language: str = "",
    job_description: str = "",
) -> ResumeLanguageChoice:
    country = " ".join(str(target_country or "").split())
    explicit_code = normalize_language(explicit_language)
    if explicit_code:
        return ResumeLanguageChoice(
            explicit_code,
            language_name(explicit_code),
            "user_override",
            country,
        )

    country_codes = _COUNTRY_LANGUAGES.get(country.casefold())
    if country_codes is None:
        # Free-text countries and territories cannot all be mapped reliably. A
        # job posting is the strongest application-specific signal when the
        # country is blank or outside the built-in map.
        detected = detect_text_language(job_description)
        if detected:
            return ResumeLanguageChoice(
                detected,
                language_name(detected),
                "job_description",
                country,
            )
        country_codes = ("en",)
    elif len(country_codes) > 1:
        detected = detect_text_language(job_description, country_codes)
        if detected:
            return ResumeLanguageChoice(
                detected,
                language_name(detected),
                "job_description",
                country,
            )
    code = country_codes[0]
    return ResumeLanguageChoice(code, language_name(code), "target_country", country)


def resume_language_options() -> tuple[tuple[str, str], ...]:
    return SUPPORTED_RESUME_LANGUAGES


def resume_labels(language: str) -> dict[str, str]:
    code = normalize_language(language) or "en"
    return dict(_RESUME_LABELS.get(code, _RESUME_LABELS["en"]))


def translated_profile_fingerprint(profile: CandidateProfile, language: str) -> str:
    payload = {
        "profile": profile.model_dump(mode="json"),
        "language": normalize_language(language) or "en",
        "translation_schema": 3,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _numeric_tokens(value: str) -> tuple[str, ...]:
    return tuple(_NUMBER_RE.findall(value or ""))


def _date_semantic_tokens(value: str) -> tuple[str, ...]:
    """Return language-independent date facts while ignoring presentation.

    Resume dates are natural-language display fields, so translating
    ``janvier 2020 – présent`` into ``January 2020 – Present`` is valid. The
    integrity check still needs to reject a changed month or year. Normalizing
    month words and current-position markers lets the translation change
    language and punctuation without changing the verified chronology.
    """

    text = str(value or "")
    normalized_text = (
        text.casefold()
        .replace("’", "'")
        .replace("'", "")
        .replace(".", " ")
    )
    tokens: list[str] = [f"number:{token}" for token in _numeric_tokens(text)]
    for word in _WORD_RE.findall(normalized_text):
        normalized_word = word.casefold()
        month = _DATE_MONTHS.get(normalized_word)
        if month:
            tokens.append(f"month:{month}")
        if normalized_word in _DATE_PRESENT_WORDS:
            tokens.append("present")
    return tuple(tokens)


def restore_translation_protected_fields(
    source: CandidateProfile,
    translated: CandidateProfile,
) -> CandidateProfile:
    """Restore non-translatable evidence fields after an AI translation.

    Models occasionally normalize punctuation, company names, IDs, or other
    protected metadata even when explicitly instructed not to. Those changes
    should not cause a complete translation to be discarded. This function
    deterministically copies verified facts back from the immutable source while
    retaining the translated narrative fields.

    Date display strings are intentionally *not* restored: month names and
    words such as "Present" must be allowed to appear in the target language.
    Their chronology is checked separately by :func:`_date_semantic_tokens`.
    """

    repaired = translated.model_copy(deep=True)
    repaired.name = source.name
    repaired.contact = source.contact.model_copy(deep=True)

    if len(repaired.experiences) == len(source.experiences):
        for source_experience, target_experience in zip(
            source.experiences, repaired.experiences
        ):
            target_experience.id = source_experience.id
            target_experience.employer = source_experience.employer
            target_experience.location = source_experience.location
            if len(target_experience.bullets) == len(source_experience.bullets):
                for source_bullet, target_bullet in zip(
                    source_experience.bullets, target_experience.bullets
                ):
                    target_bullet.id = source_bullet.id

    if len(repaired.education) == len(source.education):
        for source_item, target_item in zip(source.education, repaired.education):
            target_item.institution = source_item.institution
            target_item.location = source_item.location

    repaired.skills.tools_software = list(source.skills.tools_software)
    repaired.skills.languages = list(source.skills.languages)

    if len(repaired.supplemental_evidence) == len(source.supplemental_evidence):
        for source_item, target_item in zip(
            source.supplemental_evidence, repaired.supplemental_evidence
        ):
            target_item.id = source_item.id
            target_item.requirement_ids = list(source_item.requirement_ids)
            target_item.verified_skills = list(source_item.verified_skills)
            target_item.source = source_item.source
            target_item.experience_id = source_item.experience_id
            target_item.source_bullet_id = source_item.source_bullet_id
            target_item.placement = source_item.placement

    return repaired


def validate_translated_profile(
    source: CandidateProfile,
    translated: CandidateProfile,
    target_language: str,
) -> list[str]:
    """Return blocking translation-integrity issues.

    Translation may change natural-language fields, but it must preserve identity,
    contact details, source IDs, employers, institutions, date meaning, list
    cardinality, and every numeric token used in documented claims.
    """

    issues: list[str] = []
    if translated.name != source.name:
        issues.append("The candidate name changed during translation.")
    if translated.contact.model_dump() != source.contact.model_dump():
        issues.append("Contact information changed during translation.")

    if len(translated.experiences) != len(source.experiences):
        issues.append("The number of experience records changed during translation.")
    for index, source_experience in enumerate(source.experiences):
        if index >= len(translated.experiences):
            break
        target_experience = translated.experiences[index]
        if target_experience.id != source_experience.id:
            issues.append(f"Experience ID {source_experience.id} was not preserved.")
        if target_experience.employer != source_experience.employer:
            issues.append(f"Employer for {source_experience.id} changed during translation.")
        if target_experience.location != source_experience.location:
            issues.append(f"Location for {source_experience.id} changed during translation.")
        if _date_semantic_tokens(target_experience.dates) != _date_semantic_tokens(
            source_experience.dates
        ):
            issues.append(f"Dates for {source_experience.id} changed during translation.")
        if len(target_experience.bullets) != len(source_experience.bullets):
            issues.append(f"Bullet count for {source_experience.id} changed during translation.")
        for bullet_index, source_bullet in enumerate(source_experience.bullets):
            if bullet_index >= len(target_experience.bullets):
                break
            target_bullet = target_experience.bullets[bullet_index]
            if target_bullet.id != source_bullet.id:
                issues.append(f"Bullet ID {source_bullet.id} was not preserved.")
            if _numeric_tokens(target_bullet.text) != _numeric_tokens(source_bullet.text):
                issues.append(f"Numeric evidence changed in bullet {source_bullet.id}.")

    if len(translated.education) != len(source.education):
        issues.append("The number of education records changed during translation.")
    for index, source_item in enumerate(source.education):
        if index >= len(translated.education):
            break
        target_item = translated.education[index]
        if target_item.institution != source_item.institution:
            issues.append(f"Education institution {index + 1} changed during translation.")
        if target_item.location != source_item.location:
            issues.append(f"Education location {index + 1} changed during translation.")
        if _date_semantic_tokens(target_item.date) != _date_semantic_tokens(
            source_item.date
        ):
            issues.append(f"Education date {index + 1} changed during translation.")
        if _numeric_tokens(target_item.detail) != _numeric_tokens(source_item.detail):
            issues.append(f"Numeric evidence changed in education item {index + 1}.")

    source_skill_counts = {
        field: len(getattr(source.skills, field))
        for field in (
            "hard_skills",
            "soft_skills",
            "tools_software",
            "industry_knowledge",
            "languages",
        )
    }
    target_skill_counts = {
        field: len(getattr(translated.skills, field))
        for field in source_skill_counts
    }
    if source_skill_counts != target_skill_counts:
        issues.append("The number or category of verified skills changed during translation.")
    if translated.skills.tools_software != source.skills.tools_software:
        issues.append("Tool and software product names changed during translation.")
    if translated.skills.languages != source.skills.languages:
        issues.append("The candidate's spoken-language list changed during translation.")

    if len(translated.supplemental_evidence) != len(source.supplemental_evidence):
        issues.append("Supplemental evidence records changed during translation.")
    else:
        for source_item, target_item in zip(
            source.supplemental_evidence, translated.supplemental_evidence
        ):
            if source_item.id != target_item.id:
                issues.append(f"Supplemental evidence ID {source_item.id} changed.")
            if source_item.requirement_ids != target_item.requirement_ids:
                issues.append(f"Requirement links for {source_item.id} changed.")
            if _numeric_tokens(target_item.statement) != _numeric_tokens(source_item.statement):
                issues.append(f"Numeric evidence changed in {source_item.id}.")

    target_code = normalize_language(target_language)
    if target_code:
        language_candidates = tuple(_LANGUAGE_MARKERS)
        language_sections: list[tuple[str, str]] = [
            ("Professional summary", translated.current_summary),
            (
                "Skills",
                "\n".join(
                    translated.skills.hard_skills
                    + translated.skills.soft_skills
                    + translated.skills.industry_knowledge
                ),
            ),
        ]
        language_sections.extend(
            (
                f"Experience {index + 1}",
                "\n".join(
                    [experience.title]
                    + [bullet.text for bullet in experience.bullets]
                ),
            )
            for index, experience in enumerate(translated.experiences)
        )
        language_sections.extend(
            (
                f"Education item {index + 1}",
                "\n".join([item.credential, item.detail]),
            )
            for index, item in enumerate(translated.education)
        )
        language_sections.extend(
            (f"Supplemental evidence {index + 1}", item.statement)
            for index, item in enumerate(translated.supplemental_evidence)
        )

        for section_name, section_text in language_sections:
            detected = detect_text_language(section_text, language_candidates)
            if detected and detected != target_code:
                issues.append(
                    f"{section_name} appears to be {language_name(detected)}, not {language_name(target_code)}."
                )

    return list(dict.fromkeys(issues))

RESUME_TRANSLATION_SYSTEM = """You translate a structured CandidateProfile into one target resume language under a strict evidence-preservation policy.

Rules:
- Translate every natural-language resume field into the requested target language: current_summary, job titles, accomplishment bullets, descriptive hard skills, soft skills, industry knowledge, credential descriptions, education details, and supplemental evidence statements.
- Keep the candidate name, employer names, institution names, locations, IDs, URLs, product names, technology names, acronyms, certification brands, and spoken-language names unchanged. Contact information is redacted from the prompt and restored by the application.
- Preserve the exact chronology of every date. Translate month names and words such as "Present" into the target language, but never change a month, year, range endpoint, or current-role meaning.
- Keep tools_software and the spoken languages list exactly unchanged.
- Preserve every experience, bullet, education item, skill item, and supplemental-evidence item in the same order and category.
- Preserve every number, percentage, date, scope, technology, responsibility, and outcome. Do not add, remove, strengthen, summarize, or reinterpret facts.
- Translate job titles and credential names into the target language. The application preserves the original official wording separately as verified source evidence, so do not repeat source-language prose in the translated resume.
- Do not mix source-language prose into the translated narrative. Proper names, official terms, product names, and acronyms are allowed to remain unchanged.
- Return only the complete translated CandidateProfile."""


def build_resume_translation_prompt(
    profile: CandidateProfile,
    *,
    target_language: str,
    target_country: str = "",
) -> str:
    payload = profile.model_dump()
    payload["contact"] = {
        "location": "",
        "phone": "",
        "email": "",
        "linkedin_label": profile.contact.linkedin_label,
        "linkedin_url": "",
        "github_label": profile.contact.github_label,
        "github_url": "",
    }
    return f"""Translate the CandidateProfile below into {language_name(target_language)} for a resume application in {target_country or 'the target market'}.

The result must be a faithful language conversion, not a tailored rewrite. Keep all protected facts and source IDs unchanged. Contact values were redacted for privacy; return the contact object with those redacted values unchanged because the application restores the verified contact information afterward.

CANDIDATE PROFILE
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def resume_format_headings(language: str, resume_format: str) -> dict[str, str]:
    labels = resume_labels(language)
    format_key = str(resume_format or "standard").strip().casefold()
    if format_key == "technical":
        return {
            "summary": labels["technical_profile"],
            "skills": labels["technical_skills"],
            "experience": labels["engineering_experience"],
            "education": labels["education_certifications"],
        }
    if format_key == "career_changer":
        return {
            "summary": labels["professional_profile"],
            "skills": labels["transferable_skills"],
            "experience": labels["relevant_experience"],
            "education": labels["education"],
        }
    if format_key == "freelance":
        return {
            "summary": labels["professional_profile"],
            "skills": labels["core_capabilities"],
            "experience": labels["client_experience"],
            "education": labels["education_credentials"],
        }
    return {
        "summary": labels["professional_summary"],
        "skills": labels["skills"],
        "experience": labels["experience"],
        "education": labels["education"],
    }
