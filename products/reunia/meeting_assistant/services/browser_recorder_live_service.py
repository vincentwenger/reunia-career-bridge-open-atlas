from __future__ import annotations

import hashlib
import re
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage

from meeting_assistant.services.browser_recorder_service import BrowserRecorderService
from meeting_assistant.services.ai_cost_control_service import AICostControlService
from meeting_assistant.services.live_qa_service import LiveQAService
from meeting_assistant.services.recorder_live_state_store import RecorderLiveStateStore
from meeting_assistant.utils.exceptions import ValidationError


_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_SOURCE_MAP = {
    "microphone": "MICROPHONE",
    "speaker": "SPEAKER",
}
_CANCELLED_SESSION_TTL_SECONDS = 2 * 60 * 60
_QUESTION_SESSION_TTL_SECONDS = 2 * 60 * 60
_MAX_QUESTION_CONTEXT_CHARACTERS = 1600

# Speech-to-text punctuation is not guaranteed. These starts identify direct
# questions and common meeting/interview requests without treating ordinary
# statements containing words such as "what" as questions.
_QUESTION_START_PATTERN = re.compile(
    r"^(?:"
    r"what|when|where|why|who|whom|whose|which|how|"
    r"am|is|are|was|were|do|does|did|can|could|will|would|shall|should|"
    r"have|has|had|may|might|must|"
    r"tell\s+me|explain|describe|walk\s+me\s+through|"
    r"help\s+me\s+understand|give\s+me|"
    r"quoi|quand|où|ou|pourquoi|qui|lequel|laquelle|lesquels|lesquelles|"
    r"comment|combien|quel|quelle|quels|quelles|"
    r"est-ce\s+que|est-ce\s+qu['’]|"
    r"peux-tu|pouvez-vous|pourrais-tu|pourriez-vous|"
    r"dois-je|devrais-je|faut-il|"
    r"dis-moi|dites-moi|explique|expliquez|décris|décrivez|"
    r"aide-moi|aidez-moi|donne-moi|donnez-moi"
    r")\b",
    re.IGNORECASE,
)
_DISCOURSE_PREFIX_PATTERN = re.compile(
    r"^(?:(?:okay|ok|so|well|right|now|then|please|um|uh|and|but|"
    r"d'accord|alors|bon|bien|maintenant|ensuite|s'il\s+vous\s+plaît|"
    r"euh|et|mais)"
    r"(?:\s*,?\s+))+",
    re.IGNORECASE,
)
_INCOMPLETE_ENDINGS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "because",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "in",
    "is",
    "may",
    "might",
    "must",
    "my",
    "of",
    "on",
    "or",
    "our",
    "should",
    "that",
    "the",
    "their",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whose",
    "why",
    "will",
    "with",
    "would",
    "your",
    "à",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "cette",
    "comment",
    "dans",
    "de",
    "des",
    "du",
    "en",
    "est",
    "et",
    "la",
    "le",
    "les",
    "mais",
    "mon",
    "notre",
    "ou",
    "où",
    "par",
    "pour",
    "pourquoi",
    "quand",
    "que",
    "quel",
    "quelle",
    "qui",
    "sur",
    "un",
    "une",
    "votre",
}


class BrowserRecorderLiveService:
    """Transcribe browser audio windows and route detected questions to Live Q&A."""

    def __init__(
        self,
        recorder_service: BrowserRecorderService | None = None,
        live_qa_service: LiveQAService | None = None,
        state_store: RecorderLiveStateStore | None = None,
    ) -> None:
        self.recorder_service = recorder_service or BrowserRecorderService()
        self.live_qa_service = live_qa_service or LiveQAService()
        self.state_store = state_store or current_app.extensions["recorder_live_state_store"]

    def process_chunk(
        self,
        *,
        user_id: str,
        recording_id: str,
        chunk_id: str,
        source: str,
        sequence: int | str,
        audio_chunk: FileStorage | None,
        prepared_meeting_id: str = "",
        previous_transcript: str = "",
        question_context: str = "",
        language: str | None = None,
        live_qa_opt_in: str | bool = False,
        elapsed_seconds: str | float = 0,
    ) -> dict[str, Any]:
        recording_id = _validated_id(recording_id, "recording_id")
        chunk_id = _validated_id(chunk_id, "chunk_id")
        normalized_source = str(source or "").strip().lower()
        recorder_source = _SOURCE_MAP.get(normalized_source)
        if not recorder_source:
            raise ValidationError("The live audio source must be microphone or speaker.")

        try:
            sequence_number = int(sequence)
        except (TypeError, ValueError) as exc:
            raise ValidationError("The live audio sequence must be an integer.") from exc
        if sequence_number < 0:
            raise ValidationError("The live audio sequence cannot be negative.")

        if self.is_cancelled(user_id=user_id, recording_id=recording_id):
            return _status_payload(
                "cancelled",
                recording_id,
                chunk_id,
                normalized_source,
                sequence_number,
            )

        # Browser-recorder Live Q&A is deliberately opt-in per meeting. This is
        # independent from the desktop-client source preferences in Settings.
        if not _as_bool(live_qa_opt_in):
            return _status_payload(
                "live_qa_disabled",
                recording_id,
                chunk_id,
                normalized_source,
                sequence_number,
            )
        try:
            elapsed = max(0.0, float(elapsed_seconds or 0))
        except (TypeError, ValueError):
            elapsed = 0.0
        AICostControlService().ensure_live_qa_duration(elapsed)

        transcription = self.recorder_service.transcribe_live_upload(
            audio_chunk,
            source=recorder_source,
            user_id=user_id,
            reference_id=chunk_id,
            language=language,
        )
        raw_text = str(transcription.get("text") or "").strip()
        accepted_text = _remove_transcript_overlap(
            str(previous_transcript or "")[-4000:],
            raw_text,
            minimum_words=max(
                2,
                int(
                    current_app.config.get(
                        "RECORDER_LIVE_OVERLAP_MIN_WORDS",
                        3,
                    )
                ),
            ),
        )

        server_context = self.state_store.get_context(
            str(user_id), recording_id, normalized_source
        )
        rolling_context = str(question_context or server_context or "")

        if not accepted_text:
            payload = _status_payload(
                "no_new_speech",
                recording_id,
                chunk_id,
                normalized_source,
                sequence_number,
            )
            payload.update(
                {
                    "transcript": "",
                    "question_context": rolling_context,
                    "quality": transcription.get("quality") or {},
                }
            )
            return payload

        detected_questions, remaining_context = _extract_questions(
            rolling_context,
            accepted_text,
        )
        self.state_store.set_context(
            str(user_id),
            recording_id,
            normalized_source,
            remaining_context[-_MAX_QUESTION_CONTEXT_CHARACTERS:],
            _QUESTION_SESSION_TTL_SECONDS,
        )
        new_questions: list[str] = []
        seen_in_batch: set[str] = set()
        for question in detected_questions:
            key = _question_key(question)
            if not key or key in seen_in_batch:
                continue
            seen_in_batch.add(key)
            if self.state_store.reserve_question(
                str(user_id),
                recording_id,
                normalized_source,
                key,
                _QUESTION_SESSION_TTL_SECONDS,
            ):
                new_questions.append(question)

        if not new_questions:
            payload = _status_payload(
                "no_question",
                recording_id,
                chunk_id,
                normalized_source,
                sequence_number,
            )
            payload.update(
                {
                    "transcript": accepted_text,
                    "question_context": remaining_context,
                    "quality": transcription.get("quality") or {},
                }
            )
            return payload

        submissions: list[dict[str, str]] = []
        for question_index, question in enumerate(new_questions):
            if self.is_cancelled(user_id=user_id, recording_id=recording_id):
                return _status_payload(
                    "cancelled",
                    recording_id,
                    chunk_id,
                    normalized_source,
                    sequence_number,
                )

            entry_id = _entry_id(
                user_id=user_id,
                recording_id=recording_id,
                source=normalized_source,
                question=question,
            )
            reservation_key = _question_key(question)
            try:
                response = self.live_qa_service.submit_data(
                    user_id,
                    {
                        "origin": normalized_source,
                        "file_content": question,
                        "meeting_id": str(prepared_meeting_id or "").strip(),
                        "entry_id": entry_id,
                        "recording_id": recording_id,
                        "chunk_id": chunk_id,
                        "sequence": sequence_number,
                        "question_index": question_index,
                        "_source_enabled_override": True,
                    },
                )
            except Exception:
                self.state_store.release_question(
                    str(user_id), recording_id, normalized_source, reservation_key
                )
                raise

            if isinstance(response, str):
                self.state_store.release_question(
                    str(user_id), recording_id, normalized_source, reservation_key
                )
                payload = _status_payload(
                    "source_disabled",
                    recording_id,
                    chunk_id,
                    normalized_source,
                    sequence_number,
                )
                payload.update(
                    {
                        "transcript": accepted_text,
                        "question_context": remaining_context,
                        "message": response,
                    }
                )
                return payload

            answer = "".join(str(part) for part in response)
            submissions.append(
                {
                    "entry_id": entry_id,
                    "question": question,
                    "answer": answer,
                }
            )

        first = submissions[0]
        payload = _status_payload(
            "submitted",
            recording_id,
            chunk_id,
            normalized_source,
            sequence_number,
        )
        payload.update(
            {
                "entry_id": first["entry_id"],
                "question": first["question"],
                "answer": first["answer"],
                "questions": [item["question"] for item in submissions],
                "submissions": submissions,
                "transcript": accepted_text,
                "question_context": remaining_context,
                "quality": transcription.get("quality") or {},
            }
        )
        return payload

    @staticmethod
    def cancel_session(*, user_id: str, recording_id: str) -> None:
        recording_id = _validated_id(recording_id, "recording_id")
        current_app.extensions["recorder_live_state_store"].cancel(
            str(user_id), recording_id, _CANCELLED_SESSION_TTL_SECONDS
        )

    def is_cancelled(self, *, user_id: str, recording_id: str) -> bool:
        return self.state_store.is_cancelled(str(user_id), str(recording_id))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _status_payload(
    status: str,
    recording_id: str,
    chunk_id: str,
    source: str,
    sequence: int,
) -> dict[str, Any]:
    return {
        "status": status,
        "recording_id": recording_id,
        "chunk_id": chunk_id,
        "source": source,
        "sequence": sequence,
    }


def _validated_id(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValidationError(
            f"{field_name} must be 8 to 160 letters, numbers, dots, colons, underscores, or dashes."
        )
    return normalized


def _entry_id(*, user_id: str, recording_id: str, source: str, question: str) -> str:
    # The normalized question is part of the deterministic identifier. This is a
    # second deduplication layer if a retry is handled by another web worker.
    digest = hashlib.sha256(
        f"{user_id}\x1f{recording_id}\x1f{source}\x1f{_question_key(question)}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"browser-live-{digest[:40]}"


def _extract_questions(previous_context: str, current_text: str) -> tuple[list[str], str]:
    """Return complete questions and the unfinished question fragment, if any.

    Browser audio windows often contain ordinary conversation and Whisper may
    omit a question mark. Only direct questions or question-like requests are
    emitted. A trailing incomplete question is retained for the next window.
    """
    combined = _join_context(previous_context, current_text)
    if not combined:
        return [], ""

    questions: list[str] = []
    remaining = ""
    segments = re.findall(r"[^.!?]+(?:[.!?]+|$)", combined)

    for index, raw_segment in enumerate(segments):
        segment = raw_segment.strip()
        if not segment:
            continue
        terminal_match = re.search(r"([.!?]+)$", segment)
        terminal = terminal_match.group(1)[-1] if terminal_match else ""
        body = re.sub(r"[.!?]+$", "", segment).strip(" \t\n\r\"'“”‘’")
        candidate = _question_candidate(body)

        if terminal == "?":
            # A question mark is the strongest available signal. Preserve short
            # conversational questions such as "Your name?" even when they do
            # not begin with an interrogative word or auxiliary verb.
            explicit_candidate = candidate or _strip_discourse_prefix(body)
            if explicit_candidate:
                questions.append(_format_question(explicit_candidate))
            continue

        if not candidate:
            continue

        if terminal in {".", "!"}:
            # ASR occasionally punctuates a spoken question as a statement.
            if _looks_like_question(candidate):
                questions.append(_format_question(candidate))
            continue

        # Only the final unpunctuated segment can be continued by the next audio
        # window. Emit it when it is sufficiently complete; otherwise retain it.
        is_last = index == len(segments) - 1
        if is_last and _looks_complete_unpunctuated_question(candidate):
            questions.append(_format_question(candidate))
        elif is_last:
            remaining = candidate[-_MAX_QUESTION_CONTEXT_CHARACTERS:]

    return questions, remaining


def _join_context(previous_context: str, current_text: str) -> str:
    previous = re.sub(r"\s+", " ", str(previous_context or "")).strip()
    current = re.sub(r"\s+", " ", str(current_text or "")).strip()
    if previous and current:
        return f"{previous} {current}"[-_MAX_QUESTION_CONTEXT_CHARACTERS:]
    return (previous or current)[-_MAX_QUESTION_CONTEXT_CHARACTERS:]



def _strip_discourse_prefix(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return _DISCOURSE_PREFIX_PATTERN.sub("", value).strip(" ,:;-")

def _question_candidate(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""

    value = _DISCOURSE_PREFIX_PATTERN.sub("", value).strip(" ,:;-")
    if _QUESTION_START_PATTERN.match(value):
        return value

    # A transcript window may begin with a brief statement before the question.
    # Only search after a strong clause boundary to avoid matching statements
    # such as "I understand what you mean".
    for match in re.finditer(r"[,;:]\s*", value):
        candidate = _DISCOURSE_PREFIX_PATTERN.sub("", value[match.end():]).strip()
        if _QUESTION_START_PATTERN.match(candidate):
            return candidate
    return ""


def _looks_like_question(candidate: str) -> bool:
    return bool(_QUESTION_START_PATTERN.match(str(candidate or "").strip()))


def _looks_complete_unpunctuated_question(candidate: str) -> bool:
    words = [word for word in re.findall(r"[A-Za-z0-9']+", candidate) if word]
    if not words or not _looks_like_question(candidate):
        return False

    first = words[0].lower()
    minimum_words = 2 if first in {"what", "when", "where", "why", "who", "which", "how"} else 4
    if len(words) < minimum_words:
        return False

    last = words[-1].lower()
    if last in _INCOMPLETE_ENDINGS:
        return False

    normalized = " ".join(word.lower() for word in words)
    if normalized.endswith(("tell me", "explain to me", "walk me through", "help me understand")):
        return False
    return True


def _format_question(candidate: str) -> str:
    value = re.sub(r"\s+", " ", str(candidate or "")).strip(" \t\n\r.!?\"'“”‘’")
    if not value:
        return ""
    return value[0].upper() + value[1:] + "?"


def _question_key(question: str) -> str:
    return " ".join(
        word
        for word in (_normalize_word(value) for value in str(question or "").split())
        if word
    )


def _remove_transcript_overlap(
    previous_text: str,
    current_text: str,
    *,
    minimum_words: int = 3,
) -> str:
    """Remove the repeated prefix created by overlapping audio windows."""
    current_words = str(current_text or "").split()
    if not current_words:
        return ""

    previous_words = str(previous_text or "").split()
    if not previous_words:
        return " ".join(current_words)

    previous_normalized = [_normalize_word(word) for word in previous_words]
    current_normalized = [_normalize_word(word) for word in current_words]
    previous_normalized = [word for word in previous_normalized if word]
    current_pairs = [
        (original, normalized)
        for original, normalized in zip(current_words, current_normalized)
        if normalized
    ]
    if not current_pairs:
        return ""

    current_words = [original for original, _ in current_pairs]
    current_normalized = [normalized for _, normalized in current_pairs]

    if current_normalized == previous_normalized[-len(current_normalized):]:
        return ""

    maximum = min(len(previous_normalized), len(current_normalized))
    minimum = max(1, int(minimum_words))
    overlap = 0
    for size in range(maximum, minimum - 1, -1):
        if previous_normalized[-size:] == current_normalized[:size]:
            overlap = size
            break

    return " ".join(current_words[overlap:]).strip()


def _normalize_word(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", str(value or "").lower())
