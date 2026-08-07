from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from career_bridge.profile_context import ReusableCareerProfile
from flask import current_app
from openai import OpenAI
from werkzeug.datastructures import FileStorage

from meeting_assistant.i18n import ai_language_instruction, normalize_language
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.ai_cost_control_service import (
    AICostControlService,
    raise_if_openai_limited,
)
from meeting_assistant.services.audio_transcription_service import ShortAudioTranscriptionService
from meeting_assistant.services.application_materials_service import ApplicationMaterialsService
from meeting_assistant.services.interview_readiness_service import InterviewReadinessService
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.exceptions import (
    ExternalServiceError,
    ResourceNotFoundError,
    ValidationError,
)
from meeting_assistant.utils.json_parsing import clean_json_response

_INTERVIEW_TYPES = {
    "recruiter_screening": "Recruiter screening",
    "hiring_manager": "Hiring-manager interview",
    "behavioral": "Behavioral interview",
    "technical": "Technical interview",
    "final": "Final interview",
    "custom": "Custom practice session",
    "saved_questions": "My saved questions",
}

_DEFAULT_OPENINGS = {
    "recruiter_screening": "Please introduce yourself and explain why this opportunity is a strong next step for you.",
    "hiring_manager": "Walk me through the experience that best prepares you to succeed in this role.",
    "behavioral": "Tell me about a challenging situation at work and how you handled it.",
    "technical": "Describe the most relevant technical problem you have solved and the decisions you made.",
    "final": "Why are you the right person for this role, and what would you aim to accomplish first?",
    "custom": "What would you like the interviewer to understand first about your fit for this opportunity?",
    "saved_questions": "Please begin with the first question from your saved interview list.",
}

_SKIP_QUESTIONS = {
    "recruiter_screening": (
        "What specifically attracted you to this role and company?",
        "Which parts of your background are most relevant to this opportunity?",
        "How would you explain your current career transition to a recruiter?",
        "What are you looking for in your next role?",
    ),
    "hiring_manager": (
        "What would you prioritize during your first months in this role?",
        "Tell me about a decision you owned when the right path was not obvious.",
        "How do you collaborate with stakeholders who have competing priorities?",
        "Describe a tradeoff you made to deliver an important result.",
    ),
    "behavioral": (
        "Tell me about a conflict you helped resolve at work.",
        "Describe a time you had to deliver under a difficult deadline.",
        "Tell me about a mistake or setback and what you changed afterward.",
        "Give me an example of how you influenced others without direct authority.",
    ),
    "technical": (
        "Walk me through how you would design a reliable solution for a role-relevant problem.",
        "Describe a difficult production issue you diagnosed and resolved.",
        "How have you improved the performance or reliability of a system?",
        "Tell me about a technical tradeoff you made and how you evaluated it.",
    ),
    "final": (
        "What would success look like for you in the first 90 days?",
        "What is the biggest risk you would need to manage in this role?",
        "How would you build trust with the team and key stakeholders?",
        "What would you want the interview panel to remember about you?",
    ),
    "custom": (
        "Which experience best demonstrates your readiness for this opportunity?",
        "What concern might an interviewer have about your candidacy, and how would you address it?",
        "Describe a result that shows the value you could bring to this role.",
        "What other part of your background should we explore?",
    ),
}

_WORD_RE = re.compile(r"\b[\w’'’-]+\b", re.UNICODE)
_VAGUE_TERMS = {
    "things", "stuff", "somehow", "various", "many", "a lot", "helped", "worked on",
    "responsible for", "good", "great", "successful", "improved", "handled it",
}

_INTERVIEW_SCORECARD_CRITERIA = {
    "answer_relevance": "Answer relevance",
    "use_of_evidence": "Use of evidence",
    "star_structure": "STAR structure",
    "clarity_conciseness": "Clarity and conciseness",
    "role_alignment": "Role alignment",
    "confidence_of_delivery": "Confidence of delivery",
    "handling_follow_up_questions": "Handling of follow-up questions",
    "questions_asked_employer": "Questions asked of the employer",
}

_INTERVIEW_SCORECARD_SAFETY_NOTE = (
    "This scorecard evaluates only observable communication characteristics and confirmed "
    "content. It does not infer emotions, personality, health, protected traits, or other "
    "sensitive characteristics from voice or appearance."
)


def _mock_interview_evidence_texts(
    session: dict[str, Any],
    answer_text: str,
) -> list[str]:
    evidence = [str(answer_text or "").strip()]
    workspace_context = session.get("workspace_context") or {}
    verified = workspace_context.get("verified_candidate_evidence")
    if isinstance(verified, list):
        for item in verified[:80]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                evidence.append(text)
    return [item for item in evidence if item]


def _mock_interview_text_is_grounded(
    text: str,
    *,
    session: dict[str, Any],
    answer_text: str,
    require_overlap: bool,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    try:
        from resume_tailor.grounding import validate_candidate_claim  # type: ignore

        return not validate_candidate_claim(
            normalized,
            _mock_interview_evidence_texts(session, answer_text),
            require_overlap=require_overlap,
        )
    except Exception:
        # Evidence validation is a safety gate. If it is unavailable, reject the
        # generated candidate-facing text and use the caller's safe fallback.
        return False


def _grounded_mock_text(
    value: str,
    fallback: str,
    *,
    session: dict[str, Any],
    answer_text: str,
    require_overlap: bool,
) -> tuple[str, bool]:
    candidate = str(value or "").strip()
    if candidate and _mock_interview_text_is_grounded(
        candidate,
        session=session,
        answer_text=answer_text,
        require_overlap=require_overlap,
    ):
        return candidate, True
    return str(fallback or "").strip(), False


def _grounded_mock_list(
    values: list[str],
    fallback: list[str],
    *,
    session: dict[str, Any],
    answer_text: str,
    require_overlap: bool = False,
) -> tuple[list[str], bool]:
    grounded = [
        value
        for value in values
        if _mock_interview_text_is_grounded(
            value,
            session=session,
            answer_text=answer_text,
            require_overlap=require_overlap,
        )
    ]
    if grounded:
        return grounded, len(grounded) == len(values)
    return list(fallback), False

from .mock_interview_context import MockInterviewContextMixin
from .mock_interview_generation import MockInterviewGenerationMixin
from .mock_interview_review import MockInterviewReviewMixin
from .mock_interview_sessions import MockInterviewSessionMixin

class MockInterviewService(
    MockInterviewSessionMixin,
    MockInterviewContextMixin,
    MockInterviewGenerationMixin,
    MockInterviewReviewMixin,
):
    """Compose focused Mock Interview capabilities behind the existing service API."""

    def __init__(
        self,
        *,
        client: OpenAI | None = None,
        transcription_service: ShortAudioTranscriptionService | None = None,
        transcript_service: TranscriptService | None = None,
        user_service: UserService | None = None,
        materials_service: ApplicationMaterialsService | None = None,
        session_store: Any | None = None,
    ) -> None:
        self._client = client
        self.transcription_service = (
            transcription_service or ShortAudioTranscriptionService()
        )
        self.transcript_service = transcript_service or TranscriptService()
        self.user_service = user_service or UserService()
        self.materials_service = materials_service or ApplicationMaterialsService()
        self.session_store = session_store or current_app.extensions.get(
            "career_bridge_application_store"
        )
        if self.session_store is None:
            raise RuntimeError(
                "The canonical Career Bridge application store is required for mock interviews."
            )

def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _basic_answer_signals(
    answer: str,
    *,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    normalized = " ".join(str(answer or "").split())
    lowered = normalized.casefold()
    words = _WORD_RE.findall(normalized)
    vague_hits = sorted(term for term in _VAGUE_TERMS if term in lowered)
    duration = _bounded_float(duration_seconds, None, 0.0, 3600.0)
    pace_wpm = round(len(words) / (duration / 60.0), 1) if duration and duration > 0 else None
    return {
        "word_count": len(words),
        "duration_seconds": duration,
        "pace_wpm": pace_wpm,
        "answer_length_band": _answer_length_band(len(words)),
        "has_metric": bool(re.search(r"(?:\b\d+(?:[.,]\d+)?\s*%|\$\s*\d|\b\d{2,}\b)", normalized)),
        "has_example_language": bool(re.search(r"\b(for example|for instance|specifically|when i|in my role|one time|situation)\b", lowered)),
        "vague": bool(vague_hits) or len(words) < 25,
        "vague_terms": vague_hits[:8],
    }


def _bounded_float(
    value: Any,
    default: float | None,
    minimum: float,
    maximum: float,
) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_metric_scores(
    value: Any,
    fallback: dict[str, Any],
    *,
    question_type: str,
    role_context_available: bool,
) -> dict[str, int | None]:
    raw = value if isinstance(value, dict) else {}
    normalized: dict[str, int | None] = {}
    for key in _INTERVIEW_SCORECARD_CRITERIA:
        fallback_value = fallback.get(key) if isinstance(fallback, dict) else None
        candidate = raw.get(key, fallback_value)
        if candidate is None or str(candidate).strip().lower() in {"", "null", "none", "n/a", "not_applicable"}:
            normalized[key] = None
        else:
            normalized[key] = _bounded_int(candidate, _bounded_int(fallback_value, 50, 0, 100), 0, 100)

    if question_type not in {"challenge", "follow_up"}:
        normalized["handling_follow_up_questions"] = None
    if not role_context_available:
        normalized["role_alignment"] = None
    return normalized


def _contains_candidate_question(answer: str) -> bool:
    normalized = " ".join(str(answer or "").split()).casefold()
    return bool(
        "?" in str(answer or "")
        or re.search(r"\b(i(?:'d| would) like to ask|my question is|could you tell me|what does|how does|how do you)\b", normalized)
    )


def _employer_question_was_observed(
    session: dict[str, Any],
    current_result: dict[str, Any],
) -> bool:
    current_evaluation = (
        current_result.get("evaluation")
        if isinstance(current_result.get("evaluation"), dict)
        else {}
    )
    current_metrics = (
        current_evaluation.get("metrics")
        if isinstance(current_evaluation.get("metrics"), dict)
        else {}
    )
    if current_metrics.get("questions_asked_employer") is not None:
        return True

    for answer in session.get("answers") or []:
        if _contains_candidate_question(str(answer.get("answer") or "")):
            return True
        evaluation = answer.get("evaluation") if isinstance(answer.get("evaluation"), dict) else {}
        metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {}
        if metrics.get("questions_asked_employer") is not None:
            return True
    return False


def _answer_length_band(word_count: int) -> str:
    if word_count < 25:
        return "very_short"
    if word_count < 60:
        return "short"
    if word_count <= 180:
        return "focused"
    if word_count <= 260:
        return "long"
    return "very_long"


def _criterion_summary(key: str, score: float | None, observations: int) -> str:
    label = _INTERVIEW_SCORECARD_CRITERIA.get(key, key.replace("_", " ").title())
    if score is None or observations <= 0:
        return f"{label} was not observable in this practice session and was not included in the overall score."
    if score >= 80:
        level = "a strong observed area"
    elif score >= 65:
        level = "generally effective with room for refinement"
    elif score >= 50:
        level = "inconsistent and worth targeted practice"
    else:
        level = "a priority improvement area"
    return f"{label} was {level} across {observations} observed answer{'s' if observations != 1 else ''}."


def _dedupe_strings(values: Any) -> list[str]:
    items: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text.casefold() not in {item.casefold() for item in items}:
            items.append(text[:1000])
    return items


def _score_to_grade(score: Any) -> str:
    numeric = _bounded_int(score, 0, 0, 100)
    if numeric >= 97:
        return "A+"
    if numeric >= 93:
        return "A"
    if numeric >= 90:
        return "A-"
    if numeric >= 87:
        return "B+"
    if numeric >= 83:
        return "B"
    if numeric >= 80:
        return "B-"
    if numeric >= 77:
        return "C+"
    if numeric >= 73:
        return "C"
    if numeric >= 70:
        return "C-"
    if numeric >= 67:
        return "D+"
    if numeric >= 63:
        return "D"
    if numeric >= 60:
        return "D-"
    return "F"


def _string_list(value: Any, limit: int, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    items = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text[:500])
        if len(items) >= limit:
            break
    return items or list(fallback)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0", ""}:
        return False
    return default


def _truncate_json(value: Any, maximum: int) -> str:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw) <= maximum:
        return raw
    excerpt_length = max(0, maximum - 160)
    return json.dumps(
        {
            "context_excerpt": raw[:excerpt_length],
            "context_truncated": True,
        },
        ensure_ascii=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

from . import mock_interview_context as _mock_context
from . import mock_interview_generation as _mock_generation
from . import mock_interview_review as _mock_review
from . import mock_interview_sessions as _mock_sessions

for _module in (_mock_sessions, _mock_context, _mock_generation, _mock_review):
    _module.activate(globals())
