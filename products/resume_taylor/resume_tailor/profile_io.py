from __future__ import annotations

import json
from pathlib import Path

from .models import CandidateProfile


def load_candidate_profile(path: str | Path) -> CandidateProfile:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Candidate profile was not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Candidate profile is not valid JSON: {exc}") from exc
    return CandidateProfile.model_validate(payload)


def load_candidate_profile_bytes(data: bytes) -> CandidateProfile:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Uploaded candidate profile is not valid UTF-8 JSON: {exc}") from exc
    return CandidateProfile.model_validate(payload)

def candidate_bullet_text(profile: CandidateProfile, source_id: str) -> str | None:
    """Return verified source wording regardless of bullet lookup representation."""
    source = profile.bullet_lookup().get(source_id)
    if source is None:
        return None
    value = getattr(source, "text", source)
    return str(value)

