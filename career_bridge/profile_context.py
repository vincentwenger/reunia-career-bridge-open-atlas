"""Normalization and workflow adapters for the reusable Career Profile.

The user-facing Career Profile is stored by Réunia's UserService.  This module is
intentionally framework-independent so Job Discovery, Resume Workflow, Career
Translation, Interview Preparation, and Mock Interview all interpret the same
fields consistently.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


PROFILE_FIELD_NAMES = (
    "professional_headline",
    "current_role",
    "years_experience",
    "current_location",
    "preferred_roles",
    "industries",
    "core_skills",
    "key_accomplishments",
    "countries_worked",
    "languages",
    "target_country",
    "target_country_experience",
    "international_credentials",
    "certifications",
    "titles_needing_translation",
    "career_transition",
    "work_preferences",
    "relocation_preferences",
    "work_authorization",
    "career_goals",
    "constraints",
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _multiline_text(value: Any) -> str:
    """Normalize textarea content while preserving meaningful item boundaries."""

    lines = [_text(line) for line in str(value or "").replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line)


_MULTILINE_PROFILE_FIELDS = {
    "preferred_roles",
    "industries",
    "core_skills",
    "key_accomplishments",
    "countries_worked",
    "languages",
    "international_credentials",
    "certifications",
    "titles_needing_translation",
    "career_transition",
    "relocation_preferences",
}


def split_profile_values(value: Any, *, split_commas: bool = True) -> tuple[str, ...]:
    """Return stable, de-duplicated values from compact textarea input."""

    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "") for item in value]
    else:
        pattern = r"[,;\n]+" if split_commas else r"[;\n]+"
        raw_values = re.split(pattern, str(value or ""))
    values: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = _text(raw)
        key = item.casefold()
        if item and key not in seen:
            values.append(item)
            seen.add(key)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class ReusableCareerProfile:
    enabled: bool = True
    professional_headline: str = ""
    current_role: str = ""
    years_experience: str = ""
    current_location: str = ""
    preferred_roles: str = ""
    industries: str = ""
    core_skills: str = ""
    key_accomplishments: str = ""
    countries_worked: str = ""
    languages: str = ""
    target_country: str = ""
    target_country_experience: str = ""
    international_credentials: str = ""
    certifications: str = ""
    titles_needing_translation: str = ""
    career_transition: str = ""
    work_preferences: str = ""
    relocation_preferences: str = ""
    work_authorization: str = ""
    career_goals: str = ""
    constraints: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ReusableCareerProfile":
        raw = value if isinstance(value, Mapping) else {}
        enabled_value = raw.get("enabled", True)
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().casefold() not in {"0", "false", "no", "off"}
        else:
            enabled = bool(enabled_value)
        values = {
            name: (
                _multiline_text(raw.get(name))
                if name in _MULTILINE_PROFILE_FIELDS
                else _text(raw.get(name))
            )
            for name in PROFILE_FIELD_NAMES
        }
        return cls(enabled=enabled, **values)

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.as_prompt_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def has_context(self) -> bool:
        return self.enabled and any(self.as_prompt_dict().values())

    def as_prompt_dict(self) -> dict[str, str]:
        if not self.enabled:
            return {}
        return {
            name: getattr(self, name)
            for name in PROFILE_FIELD_NAMES
            if getattr(self, name)
        }

    @property
    def preferred_role_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.preferred_roles)

    @property
    def industry_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.industries)

    @property
    def skill_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.core_skills)

    @property
    def accomplishment_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.key_accomplishments, split_commas=False)

    @property
    def country_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.countries_worked)

    @property
    def language_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.languages)

    @property
    def credential_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.international_credentials, split_commas=False)

    @property
    def certification_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.certifications, split_commas=False)

    @property
    def translated_title_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.titles_needing_translation, split_commas=False)

    @property
    def transition_values(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return split_profile_values(self.career_transition, split_commas=False)

    @property
    def target_titles(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return tuple(dict.fromkeys(value for value in (self.current_role, *self.preferred_role_values) if value))

    @property
    def preferred_locations(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        # Preserve commas inside locations such as "Portland, Oregon".
        relocation = split_profile_values(
            self.relocation_preferences, split_commas=False
        )
        return tuple(dict.fromkeys(value for value in (self.current_location, *relocation) if value))

    @property
    def accepted_workplace_types(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        text = self.work_preferences.casefold().replace("_", "-")
        values: list[str] = []
        if "remote" in text:
            values.append("remote")
        if "hybrid" in text:
            values.append("hybrid")
        if "on-site" in text or "onsite" in text or "in office" in text:
            values.append("onsite")
        return tuple(values)

    @property
    def work_authorized(self) -> bool | None:
        if not self.enabled:
            return None
        value = self.work_authorization.casefold()
        if not value:
            return None
        negative = ("not authorized", "not currently authorized", "cannot work", "no work authorization")
        positive = ("authorized to work", "work authorized", "permanent resident", "citizen")
        if any(term in value for term in negative):
            return False
        if any(term in value for term in positive):
            return True
        return None

    @property
    def requires_sponsorship(self) -> bool | None:
        if not self.enabled:
            return None
        value = self.work_authorization.casefold()
        if not value:
            return None
        if any(term in value for term in ("require sponsorship", "requires sponsorship", "need sponsorship", "needs sponsorship")):
            return True
        if any(term in value for term in ("no sponsorship required", "does not require sponsorship", "without sponsorship")):
            return False
        return None

    def newcomer_payload(self, *, target_role: str = "") -> dict[str, Any]:
        """Map reusable fields to Resume Workflow/Career Translation context.

        This payload is context only.  Resume and interview claim grounding still
        comes from uploaded or candidate-confirmed evidence.
        """

        if not self.enabled:
            return {
                "target_role": _text(target_role),
                "career_profile_fingerprint": self.fingerprint,
            }

        roles = tuple(dict.fromkeys(value for value in (self.current_role, *self.preferred_role_values) if value))
        return {
            "professional_headline": self.professional_headline,
            "current_role": self.current_role,
            "years_experience": self.years_experience,
            "current_location": self.current_location,
            "preferred_roles": list(self.preferred_role_values),
            "core_skills": list(self.skill_values),
            "key_accomplishments": list(self.accomplishment_values),
            "countries_worked": list(self.country_values),
            "industries": list(self.industry_values),
            "roles": list(roles),
            "languages": list(self.language_values),
            "target_country": self.target_country,
            "target_country_experience": self.target_country_experience,
            "target_role": _text(target_role) or (self.preferred_role_values[0] if self.preferred_role_values else ""),
            "international_credentials": list(self.credential_values),
            "professional_certifications": list(self.certification_values),
            "unfamiliar_job_titles": list(self.translated_title_values),
            "career_transitions": list(self.transition_values),
            "us_employment_experience": self.target_country_experience,
            "work_preferences": self.work_preferences,
            "relocation_preferences": self.relocation_preferences,
            "work_authorization": self.work_authorization,
            "career_goals": self.career_goals,
            "constraints": self.constraints,
            "career_profile_fingerprint": self.fingerprint,
        }
