from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Any, Iterator

from flask import current_app
from openai import OpenAI

from meeting_assistant.i18n import ai_language_instruction, normalize_language
from meeting_assistant.prompts.scorecard_grading import (
    SCORECARD_GRADING_ALL_PROMPT,
    SCORECARD_GRADING_MICROPHONE_PROMPT,
    SCORECARD_GRADING_SPEAKER_PROMPT,
)
from meeting_assistant.prompts.meeting_insights import MEETING_INSIGHTS_PROMPT
from meeting_assistant.prompts.wins_and_improvements import WINS_AND_IMPROVEMENTS_PROMPT
from meeting_assistant.services.scoring_service import (
    FORM_GRADE_KEYS,
    calculate_overall_performance_score,
)
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.ai_cost_control_service import (
    AICostControlService,
    raise_if_openai_limited,
)
from meeting_assistant.utils.json_parsing import (
    clean_json_response,
    parse_form_metrics,
    parse_scorecard_grading,
    parse_meeting_insights,
    parse_wins_and_improvements,
)


_MICROPHONE_LABELS = frozenset({"MICROPHONE", "MIC", "USER", "ME"})
_SPEAKER_LABELS = frozenset({"SPEAKER", "OTHER", "INTERVIEWER", "PARTICIPANT"})
_ALL_GRADING_LABELS = _MICROPHONE_LABELS | _SPEAKER_LABELS

# Match known labels anywhere on a line. This supports transcript formats such as:
#   [MICROPHONE] text
#   10:25:14 [MICROPHONE]: text
#   [MICROPHONE INPUT] text
#   [SPEAKER_1] text
# It also supports more than one labeled segment on the same physical line.
_BRACKETED_LABEL_PATTERN = re.compile(
    r"\[(MICROPHONE|MIC|USER|ME|SPEAKER|OTHER|INTERVIEWER|PARTICIPANT)"
    r"(?:[\s_-]+(?:INPUT|OUTPUT|AUDIO))?(?:[\s_-]*\d+)?\]",
    re.IGNORECASE,
)

# Fallback for transcripts using unbracketed source prefixes, for example:
#   MICROPHONE: text
#   10:25 Speaker Output - text
_PLAIN_LABEL_PATTERN = re.compile(
    r"^\s*"
    r"(?:(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?)[\s|,-]*)?"
    r"(MICROPHONE|MIC|USER|ME|SPEAKER|OTHER|INTERVIEWER|PARTICIPANT)"
    r"(?:[\s_-]+(?:INPUT|OUTPUT|AUDIO))?(?:[\s_-]*\d+)?"
    r"(?:\s*[:\-–—]\s*|\s+)"
    r"(.*?)\s*$",
    re.IGNORECASE,
)

_NON_WORD_PATTERN = re.compile(r"[^a-z0-9]+", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"\b[\w’'’-]+\b", re.UNICODE)
_SPACE_PATTERN = re.compile(r"[\s_-]+")

_SCORECARD_SOURCE_ALIASES = {
    "microphone": "microphone",
    "from microphone": "microphone",
    "microphone input": "microphone",
    "mic": "microphone",
    "user": "microphone",
    "me": "microphone",
    "speaker": "speaker",
    "from speaker": "speaker",
    "speaker output": "speaker",
    "other": "speaker",
    "interviewer": "speaker",
    "participant": "speaker",
    "all": "all",
    "any": "all",
    "both": "all",
    "any or both": "all",
    "all audio": "all",
    "all audio sources": "all",
    "microphone and speaker": "all",
    "microphone or speaker": "all",
}


class TranscriptAnalysisService:
    def __init__(self, client: OpenAI | None = None) -> None:
        self._client = client

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def analyze(
        self,
        transcript: str,
        model: str,
        settings: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict:
        scorecard_source = _resolve_scorecard_source(settings)
        language = normalize_language((settings or {}).get("language"), default="en")

        if scorecard_source == "speaker":
            source_labels = _SPEAKER_LABELS
            scorecard_grading_prompt = SCORECARD_GRADING_SPEAKER_PROMPT
        elif scorecard_source == "all":
            source_labels = _ALL_GRADING_LABELS
            scorecard_grading_prompt = SCORECARD_GRADING_ALL_PROMPT
        else:
            source_labels = _MICROPHONE_LABELS
            scorecard_grading_prompt = SCORECARD_GRADING_MICROPHONE_PROMPT

        selected_source_transcript = _filter_labeled_lines(transcript, source_labels)
        grading_context_transcript = _filter_labeled_lines(transcript, _ALL_GRADING_LABELS)
        summary_detail = str((settings or {}).get("meetingSummaryDetail") or "standard")
        extract_action_items = bool((settings or {}).get("meetingExtractActionItems", True))
        generate_scorecard = bool((settings or {}).get("meetingGenerateScorecard", True))

        if user_id and current_app.config.get("AI_COMBINE_MEETING_ANALYSIS_REQUESTS", True):
            raw = self._call(
                model,
                _combined_analysis_prompt(
                    transcript=transcript,
                    scorecard_prompt=scorecard_grading_prompt,
                    summary_detail=summary_detail,
                    extract_action_items=extract_action_items,
                    generate_scorecard=generate_scorecard and bool(selected_source_transcript),
                ),
                "",
                language=language,
                user_id=user_id,
                feature="meeting_analysis_combined",
                max_output_tokens=max(
                    500,
                    int(current_app.config.get("AI_MAX_OUTPUT_TOKENS_MEETING_ANALYSIS", 2600) or 2600),
                ),
                prompt_is_complete=True,
            )
            try:
                combined = json.loads(clean_json_response(raw))
            except (TypeError, json.JSONDecodeError):
                combined = {}
            if not isinstance(combined, dict):
                combined = {}
            meeting_insights = parse_meeting_insights(
                json.dumps(combined.get("meeting_insights") or {}, ensure_ascii=False)
            )
            scorecard = parse_scorecard_grading(
                json.dumps(combined.get("scorecard") or {}, ensure_ascii=False)
            )
            wins = parse_wins_and_improvements(
                json.dumps(combined.get("coaching") or {}, ensure_ascii=False)
            )
        else:
            meeting_insights = parse_meeting_insights(
                self._call(
                    model,
                    _meeting_insights_prompt(summary_detail, extract_action_items),
                    transcript,
                    language=language,
                    user_id=user_id,
                    feature="meeting_insights",
                    max_output_tokens=900,
                )
            )
            if generate_scorecard and selected_source_transcript:
                scorecard = parse_scorecard_grading(
                    self._call(
                        model,
                        scorecard_grading_prompt,
                        grading_context_transcript,
                        language=language,
                        user_id=user_id,
                        feature="scorecard_grading",
                        max_output_tokens=1500,
                    )
                )
            else:
                scorecard = {"content_grades": [], "form_metrics": parse_form_metrics("null")}
            wins = parse_wins_and_improvements(
                self._call(
                    model,
                    WINS_AND_IMPROVEMENTS_PROMPT,
                    transcript,
                    language=language,
                    user_id=user_id,
                    feature="wins_and_improvements",
                    max_output_tokens=600,
                )
            )

        if not extract_action_items:
            meeting_insights["action_items"] = []

        if generate_scorecard and selected_source_transcript:
            content_grades = _keep_grades_from_selected_source(
                scorecard["content_grades"], selected_source_transcript
            )
            form_metrics = scorecard["form_metrics"]
        else:
            content_grades = []
            form_metrics = parse_form_metrics("null")

        evidence = _build_scorecard_evidence(selected_source_transcript, content_grades)
        scores = calculate_overall_performance_score(
            content_grades, form_metrics, evidence=evidence
        )
        _apply_form_evidence(form_metrics, evidence["form"])
        scorecard_evidence = _public_scorecard_evidence(evidence, scores)

        return {
            **meeting_insights,
            "scorecard_source": scorecard_source,
            "content_grades": content_grades,
            "form_metrics": form_metrics,
            "scorecard_evidence": scorecard_evidence,
            "scorecard_status": scorecard_evidence["overall_grade_status"],
            **scores,
            **wins,
        }

    def _call(
        self,
        model: str,
        prompt_template: str,
        transcript: str,
        *,
        language: str = "en",
        user_id: str | None = None,
        feature: str = "meeting_analysis",
        max_output_tokens: int = 1200,
        prompt_is_complete: bool = False,
    ) -> str:
        prompt = prompt_template if prompt_is_complete else prompt_template.replace(
            "{{MEETING_TRANSCRIPT}}", transcript
        ).replace("{meeting_transcript}", transcript)
        language_instruction = ai_language_instruction(language, json_values=True)
        system_content = "You are a precise meeting analysis assistant. " + language_instruction
        user_content = prompt + "\n\n" + language_instruction
        cost_control = AICostControlService()
        reservation = cost_control.reserve_text_request(
            user_id or "anonymous",
            feature=feature,
            model=model,
            prompt_characters=len(system_content) + len(user_content),
            max_output_tokens=max_output_tokens,
        )

        started_at = time.monotonic()
        try:
            request_parameters = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                "max_completion_tokens": max_output_tokens,
            }
            try:
                response = self.client.chat.completions.create(**request_parameters)
            except TypeError:
                request_parameters["max_tokens"] = request_parameters.pop(
                    "max_completion_tokens", max_output_tokens
                )
                response = self.client.chat.completions.create(**request_parameters)
            reservation.settle(
                cost_control.usage_cost_usd(model, getattr(response, "usage", None))
            )
            if user_id:
                try:
                    UsageMetricsService().record_ai_response(
                        user_id,
                        response,
                        feature=feature,
                        model=model,
                        duration_ms=int((time.monotonic() - started_at) * 1000),
                    )
                except Exception:
                    current_app.logger.exception("Could not record meeting-analysis AI usage")
            content = response.choices[0].message.content
            return (content or "").strip()
        except Exception as error:
            reservation.release()
            if user_id:
                try:
                    UsageMetricsService().record_product_event(
                        "ai_failure",
                        user_id,
                        metadata={
                            "feature": feature,
                            "model": model,
                            "duration_ms": int((time.monotonic() - started_at) * 1000),
                        },
                    )
                except Exception:
                    current_app.logger.exception("Could not record meeting-analysis AI failure")
            raise_if_openai_limited(error)
            raise


def _combined_analysis_prompt(
    *,
    transcript: str,
    scorecard_prompt: str,
    summary_detail: str,
    extract_action_items: bool,
    generate_scorecard: bool,
) -> str:
    detail_instruction = {
        "brief": "Keep the summary to 2 or 3 concise sentences.",
        "detailed": (
            "Write a detailed summary covering the main discussion, decisions, "
            "important context, risks, and next steps."
        ),
    }.get(
        str(summary_detail or "standard").strip().lower(),
        "Write a clear, moderately detailed summary in one short paragraph.",
    )
    action_instruction = (
        "Extract concrete follow-up tasks into action_items."
        if extract_action_items
        else "Return an empty action_items array."
    )
    scorecard_rules = scorecard_prompt.replace(
        "Meeting transcript:\n{{MEETING_TRANSCRIPT}}", ""
    ).strip()
    scorecard_instruction = (
        scorecard_rules
        if generate_scorecard
        else "Do not grade the scorecard. Return empty content_grades and null form values."
    )
    return f"""
Analyze the transcript once and return valid JSON only in exactly this structure:
{{
  "meeting_insights": {{
    "meeting_name": "Concise meeting title",
    "summary": "Meeting summary",
    "topics": ["Topic"],
    "action_items": ["Action item"],
    "open_questions": ["Open question"]
  }},
  "scorecard": {{
    "content_grades": [],
    "form_metrics": {{
      "pace_wpm": null, "pace_grade": null,
      "filler_words_count": null, "filler_words": null, "filler_words_grade": null,
      "power_words_count": null, "power_words": null, "power_words_grade": null,
      "negative_words_count": null, "negative_words": null, "negative_words_grade": null,
      "negative_tone_count": null, "negative_tone": null, "negative_tone_grade": null,
      "pauses_count": null, "pauses_grade": null, "overall_assessment": null
    }}
  }},
  "coaching": {{
    "key_wins": ["Key win"],
    "improvement_areas": ["Improvement area"]
  }}
}}

Meeting-insights requirements:
- {detail_instruction}
- {action_instruction}
- Return 1 to 3 concise, reusable topics.
- Do not invent decisions, actions, questions, or facts.

Scorecard requirements:
{scorecard_instruction}

Coaching requirements:
- Identify concise, actionable key wins and improvement areas.
- Use an empty list when none are supported.

Do not include Markdown, code fences, comments, or text outside the JSON.

Meeting transcript:
{transcript}
""".strip()


def _meeting_insights_prompt(summary_detail: str, extract_action_items: bool) -> str:
    detail = str(summary_detail or "standard").strip().lower()
    detail_instruction = {
        "brief": "Keep the summary to 2 or 3 concise sentences.",
        "detailed": (
            "Write a detailed summary covering the main discussion, decisions, "
            "important context, risks, and next steps."
        ),
    }.get(detail, "Write a clear, moderately detailed summary in one short paragraph.")
    action_instruction = (
        "Extract concrete follow-up tasks into action_items."
        if extract_action_items
        else "Return an empty action_items array; do not extract follow-up tasks."
    )
    return f"{MEETING_INSIGHTS_PROMPT}\n{detail_instruction}\n{action_instruction}\n"


def _resolve_scorecard_source(settings: dict[str, Any] | None) -> str:
    """Return a canonical scorecard source, including legacy setting aliases."""
    source_settings: dict[str, Any] = settings or {}
    nested = source_settings.get("settings")
    if isinstance(nested, dict):
        source_settings = nested

    value = source_settings.get(
        "scorecard_source",
        source_settings.get("scorecardSource", "microphone"),
    )

    # Backward compatibility with the original boolean proposal:
    # True meant microphone and False meant speaker.
    if isinstance(value, bool):
        return "microphone" if value else "speaker"

    normalized = _SPACE_PATTERN.sub(" ", str(value or "microphone").strip().lower())
    resolved = _SCORECARD_SOURCE_ALIASES.get(normalized)
    if resolved is None:
        raise ValueError(
            "scorecard_source must identify the microphone, speaker, or all audio sources."
        )
    return resolved


def _iter_labeled_segments(transcript: str) -> Iterator[tuple[str, str]]:
    """Yield canonical ``(label, text)`` segments from common transcript formats."""
    for raw_line in str(transcript or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matches = list(_BRACKETED_LABEL_PATTERN.finditer(line))
        if matches:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                spoken_text = line[match.end():end].strip(" \t:|-–—")
                if spoken_text:
                    yield match.group(1).upper(), spoken_text
            continue

        plain_match = _PLAIN_LABEL_PATTERN.match(line)
        if plain_match:
            spoken_text = plain_match.group(2).strip()
            if spoken_text:
                yield plain_match.group(1).upper(), spoken_text


def _filter_labeled_lines(transcript: str, allowed_labels: frozenset[str]) -> str:
    """Return canonical non-empty segments whose source is explicitly allowed."""
    return "\n".join(
        f"[{label}] {spoken_text}"
        for label, spoken_text in _iter_labeled_segments(transcript)
        if label in allowed_labels
    )


def _keep_grades_from_selected_source(
    content_grades: list[dict[str, str]],
    selected_source_transcript: str,
) -> list[dict[str, str]]:
    """Keep answers grounded predominantly in the selected source transcript.

    Exact normalized matches are accepted immediately. A high token-coverage
    fallback permits harmless punctuation changes or light model paraphrasing,
    avoiding the previous behavior where valid grades were discarded and the
    scorecard became N/A.
    """
    selected_text = " ".join(
        spoken_text
        for _, spoken_text in _iter_labeled_segments(selected_source_transcript)
    )
    normalized_source = _normalize_for_source_check(selected_text)
    if not normalized_source:
        return []

    source_counter = Counter(normalized_source.split())
    verified: list[dict[str, str]] = []

    for item in content_grades:
        normalized_answer = _normalize_for_source_check(item.get("answer", ""))
        if not normalized_answer:
            continue

        if normalized_answer in normalized_source:
            verified.append(item)
            continue

        answer_counter = Counter(normalized_answer.split())
        answer_token_count = sum(answer_counter.values())
        if not answer_token_count:
            continue

        matched_token_count = sum((answer_counter & source_counter).values())
        token_coverage = matched_token_count / answer_token_count

        # Require a clear majority of answer tokens to originate from the selected source.
        # This remains strict enough to reject an opposite-source answer while
        # allowing minor wording differences from the model.
        if token_coverage >= 0.65:
            verified.append(item)

    return verified


def _normalize_for_source_check(value: str) -> str:
    return _NON_WORD_PATTERN.sub(" ", str(value or "").lower()).strip()


def _build_scorecard_evidence(
    selected_source_transcript: str,
    content_grades: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    selected_text = " ".join(
        spoken_text
        for _, spoken_text in _iter_labeled_segments(selected_source_transcript)
    )
    analyzed_word_count = _count_words(selected_text)
    substantive_response_count = sum(
        1
        for item in content_grades
        if _count_words(str(item.get("answer") or "")) >= 5
    )

    if analyzed_word_count < 100 or substantive_response_count < 2:
        content_level = "insufficient"
    elif analyzed_word_count < 300 or substantive_response_count < 5:
        content_level = "limited"
    else:
        content_level = "reliable"

    if analyzed_word_count < 60:
        form_level = "insufficient"
    elif analyzed_word_count < 300:
        form_level = "limited"
    else:
        form_level = "reliable"

    content_ratio = min(
        analyzed_word_count / 300 if analyzed_word_count else 0.0,
        substantive_response_count / 5 if substantive_response_count else 0.0,
        1.0,
    )
    form_ratio = min(analyzed_word_count / 300 if analyzed_word_count else 0.0, 1.0)

    return {
        "content": {
            "level": content_level,
            "ratio": round(content_ratio, 4),
            "analyzed_word_count": analyzed_word_count,
            "substantive_response_count": substantive_response_count,
            "grade_status": _grade_status(content_level),
        },
        "form": {
            "level": form_level,
            "ratio": round(form_ratio, 4),
            "analyzed_word_count": analyzed_word_count,
            "grade_status": _grade_status(form_level),
        },
    }


def _apply_form_evidence(
    form_metrics: dict[str, Any],
    form_evidence: dict[str, Any],
) -> None:
    word_count = int(form_evidence.get("analyzed_word_count") or 0)
    evidence_level = str(form_evidence.get("level") or "insufficient")
    form_metrics["analyzed_word_count"] = word_count
    form_metrics["evidence_level"] = evidence_level
    form_metrics["grade_status"] = form_evidence.get("grade_status")

    rate_fields = {
        "filler_words_count": "filler_words_rate_per_100",
        "power_words_count": "power_words_rate_per_100",
        "negative_words_count": "negative_words_rate_per_100",
        "negative_tone_count": "negative_tone_rate_per_100",
        "pauses_count": "pauses_rate_per_100",
    }
    for count_key, rate_key in rate_fields.items():
        count = form_metrics.get(count_key)
        if evidence_level == "insufficient" or word_count <= 0 or count is None:
            form_metrics[rate_key] = None
            continue
        try:
            form_metrics[rate_key] = round(float(count) / word_count * 100, 2)
        except (TypeError, ValueError):
            form_metrics[rate_key] = None

    assessment = str(form_metrics.get("overall_assessment") or "").strip()
    if evidence_level == "insufficient":
        for grade_key in FORM_GRADE_KEYS:
            form_metrics[grade_key] = None
        form_metrics["pace_wpm"] = None
        form_metrics["pauses_count"] = None
        form_metrics["overall_assessment"] = (
            f"Insufficient evidence to grade communication form reliably: only "
            f"{word_count} eligible spoken words were available; at least 60 are recommended."
        )
    elif evidence_level == "limited":
        evidence_note = (
            f"Preliminary assessment based on {word_count} eligible spoken words; "
            "form grades become more reliable around 300 words."
        )
        form_metrics["overall_assessment"] = (
            f"{assessment} {evidence_note}".strip()
        )


def _public_scorecard_evidence(
    evidence: dict[str, dict[str, Any]],
    scores: dict[str, float | None],
) -> dict[str, Any]:
    content = evidence["content"]
    form = evidence["form"]
    available_sections = [
        section
        for section, score_key in (
            (content, "content_average_score"),
            (form, "form_average_score"),
        )
        if scores.get(score_key) is not None
    ]

    if not available_sections:
        overall_status = "insufficient"
        overall_level = "insufficient"
        summary = (
            "Not enough eligible meeting content was available to calculate a reliable "
            "performance score."
        )
    elif all(section.get("level") == "reliable" for section in available_sections) and len(available_sections) == 2:
        overall_status = "final"
        overall_level = "high"
        summary = "High evidence: both Content and Form have enough material for a reliable score."
    else:
        overall_status = "preliminary"
        levels = {str(section.get("level")) for section in available_sections}
        overall_level = "medium" if "reliable" in levels else "low"
        summary = (
            "Preliminary score: limited meeting evidence was available, so strong raw "
            "grades were moderated toward a neutral baseline."
        )

    return {
        "analyzed_word_count": content["analyzed_word_count"],
        "substantive_response_count": content["substantive_response_count"],
        "content_evidence_level": content["level"],
        "content_evidence_ratio": content["ratio"],
        "content_grade_status": content["grade_status"],
        "form_evidence_level": form["level"],
        "form_evidence_ratio": form["ratio"],
        "form_grade_status": form["grade_status"],
        "overall_evidence_level": overall_level,
        "overall_grade_status": overall_status,
        "summary": summary,
    }


def _grade_status(level: str) -> str:
    if level == "insufficient":
        return "insufficient"
    if level == "limited":
        return "preliminary"
    return "final"


def _count_words(value: str) -> int:
    return len(_WORD_PATTERN.findall(str(value or "")))
