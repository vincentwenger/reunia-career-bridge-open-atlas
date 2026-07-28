from __future__ import annotations

import re
import unicodedata

from .models import CandidateProfile


def safe_filename(value: str) -> str:
    """Return a portable filename stem using ASCII letters, numbers, hyphens, and underscores."""
    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized.strip())
    return re.sub(r"_+", "_", cleaned).strip("_") or "Candidate_Resume"


def final_resume_filename(
    profile: CandidateProfile,
    target_title: str,
    extension: str,
) -> str:
    """Build a concise, ATS-safe final filename without internal version labels."""
    candidate = safe_filename(profile.name)
    role = safe_filename(target_title.strip() or "Target_Role")
    ending = "_Resume"
    max_stem_length = 80
    minimum_role_length = 12
    candidate_limit = max_stem_length - len(ending) - minimum_role_length - 1
    candidate = candidate[:candidate_limit].rstrip("_-") or "Candidate"
    role_limit = max_stem_length - len(candidate) - len(ending) - 1
    role = role[:role_limit].rstrip("_-") or "Target_Role"
    suffix = extension.lstrip(".").casefold()
    return f"{candidate}_{role}{ending}.{suffix}"
