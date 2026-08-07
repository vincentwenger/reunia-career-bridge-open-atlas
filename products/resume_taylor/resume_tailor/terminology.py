"""Canonical user-facing terminology for resume and Baseline Resume concepts.

Internal field names such as ``source_profile`` and ``initial_report`` remain unchanged
so existing serialized workflows and stored application records stay compatible.
"""

IMPORTED_RESUME_LABEL = "Imported Resume"
VERIFIED_RESUME_EVIDENCE_LABEL = "Verified Resume Evidence"
CAREER_BASELINE_RESUME_LABEL = "Baseline Resume"
APPLICATION_BASELINE_LABEL = "Application Baseline"
TARGET_MARKET_REVIEW_LABEL = "Target-Market Review"

LEGACY_RESUME_VERSION_LABELS = {
    "Initial Resume": APPLICATION_BASELINE_LABEL,
    "Career Baseline Resume": CAREER_BASELINE_RESUME_LABEL,
}
