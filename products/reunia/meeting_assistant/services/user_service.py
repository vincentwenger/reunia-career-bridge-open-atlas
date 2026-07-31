from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError
from flask import current_app

from meeting_assistant.i18n import normalize_language
from meeting_assistant.repositories.user_repository import UserRepository
from meeting_assistant.utils.exceptions import DatabaseError, ResourceNotFoundError, ValidationError


_AI_COACHING_ANSWER_STYLE_ALIASES = {
    "": "balanced",
    "balanced": "balanced",
    "default": "balanced",
    "concise": "concise",
    "brief": "concise",
    "detailed": "detailed",
    "bullet points": "bullet_points",
    "bullet point": "bullet_points",
    "bullets": "bullet_points",
    "step by step": "step_by_step",
    "action oriented": "action_oriented",
    "professional": "professional",
}
_AI_COACHING_RESPONSE_MODE_ALIASES = {
    "": "ready_to_say",
    "ready to say": "ready_to_say",
    "readytosay": "ready_to_say",
    "direct": "ready_to_say",
    "direct answer": "ready_to_say",
    "concise structured action": "concise_structured_action",
    "concise structured action oriented": "concise_structured_action",
    "structured action": "concise_structured_action",
    "coaching": "coaching",
    "coaching guidance": "coaching",
    "guidance": "coaching",
}
_AI_COACHING_CONTEXT_FIELDS = {
    "answer_style",
    "response_mode",
    "audio_response_instructions",
    "clipboard_response_instructions",
}


def _normalized_preference_token(value: Any) -> str:
    return " ".join(
        str(value or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )


def _normalize_ai_coaching_answer_style(value: Any, *, strict: bool = False) -> str:
    normalized = _AI_COACHING_ANSWER_STYLE_ALIASES.get(_normalized_preference_token(value))
    if normalized is not None:
        return normalized
    if strict:
        raise ValueError(
            "Answer style must be balanced, concise, detailed, bullet points, step by step, action oriented, or professional."
        )
    return "balanced"


def _normalize_ai_coaching_response_mode(value: Any, *, strict: bool = False) -> str:
    normalized = _AI_COACHING_RESPONSE_MODE_ALIASES.get(_normalized_preference_token(value))
    if normalized is not None:
        return normalized
    if strict:
        raise ValueError(
            "Response mode must be ready-to-say, concise structured action, or coaching."
        )
    return "ready_to_say"


_AI_MODEL_PRESET_ALIASES = {
    "fast": "fast",
    "fast model": "fast",
    "speed": "fast",
    "low latency": "fast",
    "balanced": "balanced",
    "balanced model": "balanced",
    "recommended": "balanced",
    "smart": "balanced",
    "smart model": "balanced",
    "advanced": "advanced",
    "advanced model": "advanced",
    "strongest": "advanced",
}


def _configured_ai_model_presets() -> dict[str, str]:
    configured = current_app.config.get("AI_MODEL_PRESETS")
    if isinstance(configured, dict):
        presets = {
            key: str(configured.get(key) or "").strip()
            for key in ("fast", "balanced", "advanced")
        }
    else:
        presets = {
            "fast": str(
                current_app.config.get("AI_MODEL_FAST") or "gpt-4o-mini"
            ).strip(),
            "balanced": str(
                current_app.config.get("AI_MODEL_BALANCED")
                or current_app.config.get("DEFAULT_AI_MODEL")
                or "gpt-5.4-mini"
            ).strip(),
            "advanced": str(
                current_app.config.get("AI_MODEL_ADVANCED") or "gpt-5.4"
            ).strip(),
        }

    if not all(presets.values()):
        raise ValidationError("All AI model presets must map to a model ID.")
    return presets


def _normalize_ai_model_preset(value: Any, default: str = "balanced") -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    normalized = " ".join(normalized.replace("-", " ").split())
    if not normalized:
        normalized = default

    preset = _AI_MODEL_PRESET_ALIASES.get(normalized)
    if preset:
        return preset
    raise ValueError("AI model preference must be fast, balanced, or advanced.")


def _preset_for_ai_model(model: Any) -> str:
    presets = _configured_ai_model_presets()
    normalized_model = str(model or "").strip()
    for preset, model_id in presets.items():
        if normalized_model == model_id:
            return preset

    try:
        return _normalize_ai_model_preset(
            current_app.config.get("DEFAULT_AI_MODEL_PRESET"),
            default="fast",
        )
    except ValueError:
        return "balanced"


def _maximum_ai_model_preset() -> str | None:
    configured = current_app.config.get("AI_MAX_MODEL_PRESET")
    if configured in (None, ""):
        return None
    try:
        return _normalize_ai_model_preset(configured, default="fast")
    except ValueError:
        return "fast"


def _limit_ai_model_selection(preset: str, model_id: str) -> tuple[str, str]:
    maximum = _maximum_ai_model_preset()
    if maximum is None:
        return preset, model_id
    order = {"fast": 0, "balanced": 1, "advanced": 2}
    if order[preset] <= order[maximum]:
        return preset, model_id
    presets = _configured_ai_model_presets()
    return maximum, presets[maximum]


def _resolve_ai_model_selection(value: Any) -> tuple[str, str]:
    presets = _configured_ai_model_presets()
    normalized_value = str(value or "").strip()

    # New clients submit a user-facing preset; older clients may still submit one
    # of the configured model IDs directly.
    try:
        requested_preset = _normalize_ai_model_preset(normalized_value)
    except ValueError:
        requested_preset = None

    if requested_preset is not None:
        limited_preset, limited_model = _limit_ai_model_selection(
            requested_preset,
            presets[requested_preset],
        )
        if limited_preset != requested_preset:
            raise ValueError(
                f"The {requested_preset.title()} model is disabled by the Réunia cost policy. "
                f"The strongest enabled preset is {limited_preset.title()}."
            )
        return limited_preset, limited_model

    for preset, model_id in presets.items():
        if normalized_value == model_id:
            limited_preset, limited_model = _limit_ai_model_selection(preset, model_id)
            if limited_preset != preset:
                raise ValueError(
                    f"The {preset.title()} model is disabled by the Réunia cost policy. "
                    f"The strongest enabled preset is {limited_preset.title()}."
                )
            return limited_preset, limited_model

    raise ValueError(
        "AI model preference must be Fast, Balanced, or Advanced."
    )


_LIVE_QA_ANSWER_UPDATE_FREQUENCY_ALIASES = {
    "fast": "fast",
    "faster": "fast",
    "faster updates": "fast",
    "responsive": "fast",
    "balanced": "balanced",
    "recommended": "balanced",
    "efficient": "efficient",
    "lower resource usage": "efficient",
    "resource efficient": "efficient",
    "slow": "efficient",
    "slower": "efficient",
}

_LIVE_QA_ANSWER_UPDATE_PROFILES = {
    "fast": {
        "persist_interval_seconds": 1.0,
        "stream_interval_seconds": 1.0,
        "max_cache_age_seconds": 1.0,
    },
    "balanced": {
        "persist_interval_seconds": 2.0,
        "stream_interval_seconds": 2.0,
        "max_cache_age_seconds": 2.0,
    },
    "efficient": {
        "persist_interval_seconds": 5.0,
        "stream_interval_seconds": 5.0,
        "max_cache_age_seconds": 5.0,
    },
}


def _normalize_live_qa_answer_update_frequency(
    value: Any,
    default: str = "balanced",
) -> str:
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    normalized = " ".join(normalized.split())
    if not normalized:
        normalized = default

    frequency = _LIVE_QA_ANSWER_UPDATE_FREQUENCY_ALIASES.get(normalized)
    if frequency:
        return frequency
    raise ValueError(
        "Live answer update frequency must be fast, balanced, or efficient."
    )


def live_qa_answer_update_profile(value: Any) -> dict[str, float]:
    """Return safe timing values for a user's Live Q&A refresh preference."""
    frequency = _normalize_live_qa_answer_update_frequency(value)
    return dict(_LIVE_QA_ANSWER_UPDATE_PROFILES[frequency])


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

_DATA_RETENTION_DAY_OPTIONS = {0, 7, 30, 90, 365}
_SHARE_EXPIRATION_DAY_OPTIONS = {0, 7, 30, 90}
_MEETING_SUMMARY_DETAIL_OPTIONS = {"brief", "standard", "detailed"}


_MOCK_INTERVIEW_QUESTION_SET_FIELD = "mock_interview_question_sets"
_MAX_MOCK_INTERVIEW_QUESTION_SETS = 20
_MAX_MOCK_INTERVIEW_QUESTIONS = 20
_MAX_MOCK_INTERVIEW_QUESTION_LENGTH = 500
_MAX_MOCK_INTERVIEW_SET_NAME_LENGTH = 120


def _normalize_mock_interview_question_sets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value[:_MAX_MOCK_INTERVIEW_QUESTION_SETS]:
        if not isinstance(item, dict):
            continue
        set_id = str(item.get("id") or "").strip()
        name = " ".join(str(item.get("name") or "").split())[
            :_MAX_MOCK_INTERVIEW_SET_NAME_LENGTH
        ]
        questions_raw = item.get("questions")
        if (
            not set_id
            or set_id in seen_ids
            or not name
            or not isinstance(questions_raw, list)
        ):
            continue
        questions: list[str] = []
        for question in questions_raw[:_MAX_MOCK_INTERVIEW_QUESTIONS]:
            text = " ".join(str(question or "").split())[
                :_MAX_MOCK_INTERVIEW_QUESTION_LENGTH
            ]
            if text:
                questions.append(text)
        if not questions:
            continue
        seen_ids.add(set_id)
        normalized.append(
            {
                "id": set_id,
                "name": name,
                "questions": questions,
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    normalized.sort(
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    return normalized


def default_user_settings() -> dict[str, Any]:
    presets = _configured_ai_model_presets()
    default_model = str(current_app.config.get("DEFAULT_AI_MODEL") or "").strip()
    if not default_model:
        try:
            default_preset = _normalize_ai_model_preset(
                current_app.config.get("DEFAULT_AI_MODEL_PRESET"),
                default="fast",
            )
        except ValueError:
            default_preset = "fast"
        default_model = presets[default_preset]

    return {
        "aiModel": default_model,
        "retentionHours": 1,
        "liveQaAnswerUpdateFrequency": "efficient",
        "aiClipboard": False,
        "aiSpeaker": False,
        "aiMicrophone": False,
        "scorecard_source": "all",
        "language": "en",
        "aiCoachingAnswerStyle": "balanced",
        "aiCoachingResponseMode": "ready_to_say",
        "aiCoachingAudioInstructions": "",
        "aiCoachingClipboardInstructions": "",
        "meetingRetentionDays": 7,
        "documentRetentionDays": 7,
        "shareDefaultExpirationDays": 30,
        "shareRequirePassword": False,
        "shareAllowDownload": False,
        "shareIncludeScorecard": False,
        "meetingSummaryDetail": "brief",
        "meetingExtractActionItems": True,
        "meetingGenerateScorecard": True,
    }


def default_assistant_context() -> dict[str, Any]:
    return {
        "enabled": True,
        # Reusable Career Profile fields.
        "professional_headline": "",
        "current_role": "",
        "years_experience": "",
        "current_location": "",
        "preferred_roles": "",
        "industries": "",
        "core_skills": "",
        "key_accomplishments": "",
        "countries_worked": "",
        "languages": "",
        "target_country": "",
        "target_country_experience": "",
        "international_credentials": "",
        "certifications": "",
        "titles_needing_translation": "",
        "career_transition": "",
        "work_preferences": "",
        "relocation_preferences": "",
        "work_authorization": "",
        "career_goals": "",
        "constraints": "",
        # Legacy AI-context fields are retained for backward compatibility and
        # preserved when the new Career Profile form is saved.
        "company": "",
        "reference_link": "",
        "role": "",
        "type": "",
        "domain": "",
        "audience": "",
        "objective": "",
        "free_text": "",
    }


_ASSISTANT_CONTEXT_ALIASES = {
    "enabled": ("enabled", "use_context"),
    "professional_headline": ("professional_headline", "profile_professional_headline"),
    "current_role": ("current_role", "profile_current_role"),
    "years_experience": ("years_experience", "profile_years_experience"),
    "current_location": ("current_location", "profile_current_location"),
    "preferred_roles": ("preferred_roles", "profile_preferred_roles"),
    "industries": ("industries", "profile_industries"),
    "core_skills": ("core_skills", "profile_core_skills"),
    "key_accomplishments": ("key_accomplishments", "profile_key_accomplishments"),
    "countries_worked": ("countries_worked", "profile_countries_worked"),
    "languages": ("languages", "profile_languages"),
    "target_country": ("target_country", "profile_target_country"),
    "target_country_experience": (
        "target_country_experience",
        "profile_target_country_experience",
    ),
    "international_credentials": (
        "international_credentials",
        "profile_international_credentials",
    ),
    "certifications": ("certifications", "profile_certifications"),
    "titles_needing_translation": (
        "titles_needing_translation",
        "profile_titles_needing_translation",
    ),
    "career_transition": ("career_transition", "profile_career_transition"),
    "work_preferences": ("work_preferences", "profile_work_preferences"),
    "relocation_preferences": (
        "relocation_preferences",
        "profile_relocation_preferences",
    ),
    "work_authorization": ("work_authorization", "profile_work_authorization"),
    "career_goals": ("career_goals", "profile_career_goals"),
    "constraints": ("constraints", "profile_constraints"),
    "company": ("company", "context_company", "assistant_context_company"),
    "reference_link": (
        "reference_link",
        "context_reference_link",
        "assistant_context_reference_link",
    ),
    "role": ("role", "context_role", "assistant_context_role"),
    "type": ("type", "context_type", "assistant_context_type"),
    "domain": ("domain", "context_domain", "assistant_context_domain"),
    "audience": ("audience", "context_audience", "assistant_context_audience"),
    "answer_style": (
        "answer_style",
        "context_answer_style",
        "assistant_context_answer_style",
    ),
    "response_mode": (
        "response_mode",
        "context_response_mode",
        "assistant_context_response_mode",
    ),
    "audio_response_instructions": (
        "audio_response_instructions",
        "context_audio_response_instructions",
        "assistant_context_audio_response_instructions",
    ),
    "clipboard_response_instructions": (
        "clipboard_response_instructions",
        "context_clipboard_response_instructions",
        "assistant_context_clipboard_response_instructions",
    ),
    "objective": ("objective", "context_objective", "assistant_context_objective"),
    "free_text": ("free_text", "context_free_text", "assistant_context_free_text"),
}


class UserService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            return self.repository.get_by_id(user_id)
        except ClientError as exc:
            raise DatabaseError("Failed to retrieve the user.") from exc

    def get_settings(self, user_id: str) -> dict[str, Any]:
        user = self.get_user(user_id) or {}
        stored = user.get("settings", {})
        defaults = default_user_settings()
        defaults.update({key: value for key, value in stored.items() if value is not None})

        # Migrate the four former Career Profile response preferences without
        # changing the stored profile. They are persisted under Settings the next
        # time the user saves AI Coaching Preferences.
        legacy_context = _normalize_assistant_context(user.get("assistant_context", {}))
        legacy_preference_fallbacks = {
            "aiCoachingAnswerStyle": legacy_context.get("answer_style", ""),
            "aiCoachingResponseMode": legacy_context.get("response_mode", ""),
            "aiCoachingAudioInstructions": legacy_context.get("audio_response_instructions", ""),
            "aiCoachingClipboardInstructions": legacy_context.get("clipboard_response_instructions", ""),
        }
        for setting_key, legacy_value in legacy_preference_fallbacks.items():
            if setting_key not in stored and legacy_value not in (None, ""):
                defaults[setting_key] = legacy_value

        # Transcription language is configured locally by the desktop client.
        defaults.pop("whisperLanguage", None)
        defaults.pop("whisper_language", None)

        # Legacy meeting-context settings are no longer exposed in Career Profile.
        defaults.pop("chatGPTRole", None)
        defaults.pop("chatGPT_role", None)
        defaults.pop("chatGPTCompany", None)
        defaults.pop("chatGPT_company", None)
        defaults.pop("chatGPTLink", None)
        defaults.pop("chatGPT_link", None)

        # Legacy prompt settings are superseded by Settings → AI Coaching Preferences.
        defaults.pop("chatGPTPromptAudio", None)
        defaults.pop("chatGPT_prompt_audio", None)
        defaults.pop("chatGPTPromptClipboard", None)
        defaults.pop("chatGPT_prompt_clipboard", None)

        # The UI uses a stable, user-friendly preset while runtime services keep
        # consuming the concrete model ID stored in aiModel.
        defaults.pop("aiModelPreset", None)
        defaults["aiModel"] = str(
            defaults.get("aiModel") or current_app.config.get("DEFAULT_AI_MODEL") or ""
        ).strip()
        defaults["aiModelPreset"] = _preset_for_ai_model(defaults["aiModel"])
        defaults["aiModelPreset"], defaults["aiModel"] = _limit_ai_model_selection(
            defaults["aiModelPreset"], defaults["aiModel"]
        )

        try:
            defaults["retentionHours"] = max(1, int(defaults.get("retentionHours", 1)))
        except (TypeError, ValueError):
            defaults["retentionHours"] = 1

        stored_update_frequency = stored.get(
            "liveQaAnswerUpdateFrequency",
            stored.get(
                "live_qa_answer_update_frequency",
                defaults.get("liveQaAnswerUpdateFrequency", "efficient"),
            ),
        )
        try:
            defaults["liveQaAnswerUpdateFrequency"] = (
                _normalize_live_qa_answer_update_frequency(stored_update_frequency)
            )
        except ValueError:
            defaults["liveQaAnswerUpdateFrequency"] = "efficient"
        defaults.pop("live_qa_answer_update_frequency", None)

        defaults["scorecard_source"] = _normalize_scorecard_source(
            defaults.get("scorecard_source"),
            default="microphone",
        )
        defaults["language"] = normalize_language(defaults.get("language"), default="en")
        defaults["aiCoachingAnswerStyle"] = _normalize_ai_coaching_answer_style(
            defaults.get("aiCoachingAnswerStyle")
        )
        defaults["aiCoachingResponseMode"] = _normalize_ai_coaching_response_mode(
            defaults.get("aiCoachingResponseMode")
        )
        defaults["aiCoachingAudioInstructions"] = str(
            defaults.get("aiCoachingAudioInstructions") or ""
        ).strip()
        defaults["aiCoachingClipboardInstructions"] = str(
            defaults.get("aiCoachingClipboardInstructions") or ""
        ).strip()
        defaults["meetingRetentionDays"] = _normalized_day_option(
            defaults.get("meetingRetentionDays"),
            _DATA_RETENTION_DAY_OPTIONS,
            default=0,
        )
        defaults["documentRetentionDays"] = _normalized_day_option(
            defaults.get("documentRetentionDays"),
            _DATA_RETENTION_DAY_OPTIONS,
            default=0,
        )
        defaults["shareDefaultExpirationDays"] = _normalized_day_option(
            defaults.get("shareDefaultExpirationDays"),
            _SHARE_EXPIRATION_DAY_OPTIONS,
            default=30,
        )
        for key in (
            "shareRequirePassword",
            "shareAllowDownload",
            "shareIncludeScorecard",
            "meetingExtractActionItems",
            "meetingGenerateScorecard",
        ):
            defaults[key] = _normalized_boolean(defaults.get(key))
        summary_detail = str(defaults.get("meetingSummaryDetail") or "standard").strip().lower()
        defaults["meetingSummaryDetail"] = (
            summary_detail
            if summary_detail in _MEETING_SUMMARY_DETAIL_OPTIONS
            else "standard"
        )

        return defaults

    def get_ai_coaching_preferences(self, user_id: str) -> dict[str, str]:
        settings = self.get_settings(user_id)
        return {
            "answer_style": settings["aiCoachingAnswerStyle"],
            "response_mode": settings["aiCoachingResponseMode"],
            "audio_response_instructions": settings["aiCoachingAudioInstructions"],
            "clipboard_response_instructions": settings["aiCoachingClipboardInstructions"],
        }

    def get_assistant_context(self, user_id: str) -> dict[str, Any]:
        user = self.get_user(user_id) or {}
        stored = user.get("assistant_context", {})
        if not isinstance(stored, dict):
            stored = {}
        context = _normalize_assistant_context(stored)
        # Keep existing AI services compatible while the source of these values is
        # now Settings → AI Coaching Preferences rather than Career Profile.
        context.update(self.get_ai_coaching_preferences(user_id))
        return context

    def update_assistant_context(
        self,
        user_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValidationError("Assistant context must be a JSON object.")

        user = self.get_user(user_id) or {}
        current = _normalize_assistant_context(user.get("assistant_context", {}))

        for destination, source_names in _ASSISTANT_CONTEXT_ALIASES.items():
            # Response preferences are now owned by Settings and cannot be changed
            # through the Career Profile endpoint, including by older clients.
            if destination in _AI_COACHING_CONTEXT_FIELDS:
                continue
            for source_name in source_names:
                if source_name in data:
                    current[destination] = data[source_name]
                    break

        normalized = _normalize_assistant_context(current)
        reference_link = normalized.get("reference_link", "")
        if reference_link and not reference_link.lower().startswith(("http://", "https://")):
            raise ValidationError(
                "Reference Link must start with http:// or https://."
            )

        try:
            self.repository.update_fields(
                user_id,
                {"assistant_context": normalized},
            )
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise ResourceNotFoundError("User not found.") from exc
            raise DatabaseError("Failed to update assistant context.") from exc

        return normalized

    def list_mock_interview_question_sets(self, user_id: str) -> list[dict[str, Any]]:
        user = self.get_user(user_id) or {}
        return _normalize_mock_interview_question_sets(
            user.get(_MOCK_INTERVIEW_QUESTION_SET_FIELD)
        )

    def get_mock_interview_question_set(
        self,
        user_id: str,
        question_set_id: str,
    ) -> dict[str, Any]:
        normalized_id = str(question_set_id or "").strip()
        for item in self.list_mock_interview_question_sets(user_id):
            if item["id"] == normalized_id:
                return item
        raise ResourceNotFoundError("Saved interview question list not found.")

    def save_mock_interview_question_set(
        self,
        user_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValidationError("Question list must be a JSON object.")

        name = " ".join(str(data.get("name") or "").split())
        if not name:
            raise ValidationError("Give the question list a name.")
        if len(name) > _MAX_MOCK_INTERVIEW_SET_NAME_LENGTH:
            raise ValidationError(
                f"Question list name must contain {_MAX_MOCK_INTERVIEW_SET_NAME_LENGTH} characters or fewer."
            )

        raw_questions = data.get("questions")
        if not isinstance(raw_questions, list):
            raise ValidationError("Questions must be provided as a list.")
        questions: list[str] = []
        for raw_question in raw_questions:
            question = " ".join(str(raw_question or "").split())
            if not question:
                continue
            if len(question) > _MAX_MOCK_INTERVIEW_QUESTION_LENGTH:
                raise ValidationError(
                    f"Each question must contain {_MAX_MOCK_INTERVIEW_QUESTION_LENGTH} characters or fewer."
                )
            questions.append(question)
        if not questions:
            raise ValidationError("Add at least one interview question.")
        if len(questions) > _MAX_MOCK_INTERVIEW_QUESTIONS:
            raise ValidationError(
                f"A saved list can contain at most {_MAX_MOCK_INTERVIEW_QUESTIONS} questions."
            )

        question_sets = self.list_mock_interview_question_sets(user_id)
        requested_id = str(data.get("id") or "").strip()
        now = datetime.now(timezone.utc).isoformat()
        existing_index = next(
            (
                index
                for index, item in enumerate(question_sets)
                if item["id"] == requested_id
            ),
            None,
        )
        if requested_id and existing_index is None:
            raise ResourceNotFoundError("Saved interview question list not found.")

        if existing_index is None:
            if len(question_sets) >= _MAX_MOCK_INTERVIEW_QUESTION_SETS:
                raise ValidationError(
                    f"You can save at most {_MAX_MOCK_INTERVIEW_QUESTION_SETS} interview question lists."
                )
            saved = {
                "id": f"questions-{uuid4().hex}",
                "name": name,
                "questions": questions,
                "created_at": now,
                "updated_at": now,
            }
            question_sets.insert(0, saved)
        else:
            existing = question_sets.pop(existing_index)
            saved = {
                **existing,
                "name": name,
                "questions": questions,
                "updated_at": now,
            }
            question_sets.insert(0, saved)

        try:
            self.repository.update_fields(
                user_id,
                {_MOCK_INTERVIEW_QUESTION_SET_FIELD: question_sets},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("User not found.") from exc
            raise DatabaseError("Failed to save the interview question list.") from exc
        return saved

    def delete_mock_interview_question_set(
        self,
        user_id: str,
        question_set_id: str,
    ) -> dict[str, Any]:
        normalized_id = str(question_set_id or "").strip()
        question_sets = self.list_mock_interview_question_sets(user_id)
        retained = [item for item in question_sets if item["id"] != normalized_id]
        if len(retained) == len(question_sets):
            raise ResourceNotFoundError("Saved interview question list not found.")
        try:
            self.repository.update_fields(
                user_id,
                {_MOCK_INTERVIEW_QUESTION_SET_FIELD: retained},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("User not found.") from exc
            raise DatabaseError("Failed to delete the interview question list.") from exc
        return {"status": "deleted", "id": normalized_id}

    def update_profile(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict) or not data:
            raise ValidationError("Missing request body.")

        source_map = {
            "full_name": ("Full Name", "full_name"),
            "job_title": ("Job Title", "job_title"),
            "dob": ("Date of Birth", "dob"),
            "phone_number": ("Phone Number", "phone_number"),
            "address": ("Address", "address"),
        }
        limits = {
            "full_name": 120,
            "job_title": 120,
            "dob": 10,
            "phone_number": 40,
            "address": 500,
        }
        fields: dict[str, str] = {}
        for destination, source_names in source_map.items():
            for source_name in source_names:
                if source_name in data:
                    value = str(data[source_name] or "").strip()
                    if len(value) > limits[destination]:
                        label = destination.replace("_", " ").title()
                        raise ValidationError(
                            f"{label} must contain {limits[destination]} characters or fewer."
                        )
                    fields[destination] = value
                    break

        if not fields:
            return {}
        if "full_name" in fields and not fields["full_name"]:
            raise ValidationError("Full name cannot be empty.")
        if fields.get("dob"):
            try:
                birth_date = datetime.strptime(fields["dob"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValidationError("Date of Birth must use YYYY-MM-DD.") from exc
            today = datetime.now(timezone.utc).date()
            if birth_date > today or birth_date.year < 1900:
                raise ValidationError("Please enter a valid Date of Birth.")

        try:
            return self.repository.update_fields(user_id, fields)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ResourceNotFoundError("User not found.") from exc
            raise DatabaseError("Failed to update the profile.") from exc

    def update_settings(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings(user_id)

        mappings = {
            "retentionHours": "retentionHours",
            "liveQaAnswerUpdateFrequency": "liveQaAnswerUpdateFrequency",
            "live_qa_answer_update_frequency": "liveQaAnswerUpdateFrequency",
            "autoAskClipboard": "aiClipboard",
            "autoAskSpeaker": "aiSpeaker",
            "autoAskMicrophone": "aiMicrophone",
            "aiClipboard": "aiClipboard",
            "aiSpeaker": "aiSpeaker",
            "aiMicrophone": "aiMicrophone",
            "scorecard_source": "scorecard_source",
            "meetingRetentionDays": "meetingRetentionDays",
            "documentRetentionDays": "documentRetentionDays",
            "shareDefaultExpirationDays": "shareDefaultExpirationDays",
            "shareRequirePassword": "shareRequirePassword",
            "shareAllowDownload": "shareAllowDownload",
            "shareIncludeScorecard": "shareIncludeScorecard",
            "meetingSummaryDetail": "meetingSummaryDetail",
            "meetingExtractActionItems": "meetingExtractActionItems",
            "meetingGenerateScorecard": "meetingGenerateScorecard",
            "language": "language",
            "appLanguage": "language",
            "locale": "language",
            "aiCoachingAnswerStyle": "aiCoachingAnswerStyle",
            "answer_style": "aiCoachingAnswerStyle",
            "aiCoachingResponseMode": "aiCoachingResponseMode",
            "response_mode": "aiCoachingResponseMode",
            "aiCoachingAudioInstructions": "aiCoachingAudioInstructions",
            "audio_response_instructions": "aiCoachingAudioInstructions",
            "aiCoachingClipboardInstructions": "aiCoachingClipboardInstructions",
            "clipboard_response_instructions": "aiCoachingClipboardInstructions",
        }

        model_selection = None
        if data.get("aiModelPreset") is not None:
            model_selection = data.get("aiModelPreset")
        elif data.get("aiModel") is not None:
            # Backward compatibility for older Settings clients.
            model_selection = data.get("aiModel")

        if model_selection is not None:
            try:
                preset, model_id = _resolve_ai_model_selection(model_selection)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            current["aiModelPreset"] = preset
            current["aiModel"] = model_id

        for source, destination in mappings.items():
            if source in data and data[source] is not None:
                current[destination] = data[source]

        try:
            current["retentionHours"] = max(
                1,
                int(current.get("retentionHours", 1)),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "retentionHours must be a positive integer."
            ) from exc

        try:
            current["liveQaAnswerUpdateFrequency"] = (
                _normalize_live_qa_answer_update_frequency(
                    current.get("liveQaAnswerUpdateFrequency"),
                )
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        try:
            current["scorecard_source"] = _normalize_scorecard_source(
                current.get("scorecard_source"),
            )
        except ValueError as exc:
            raise ValidationError(
                "scorecard_source must identify the microphone, speaker, or all audio sources."
            ) from exc

        try:
            current["aiCoachingAnswerStyle"] = _normalize_ai_coaching_answer_style(
                current.get("aiCoachingAnswerStyle"), strict=True
            )
            current["aiCoachingResponseMode"] = _normalize_ai_coaching_response_mode(
                current.get("aiCoachingResponseMode"), strict=True
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        current["aiCoachingAudioInstructions"] = str(
            current.get("aiCoachingAudioInstructions") or ""
        ).strip()[:4000]
        current["aiCoachingClipboardInstructions"] = str(
            current.get("aiCoachingClipboardInstructions") or ""
        ).strip()[:4000]

        requested_language = str(current.get("language", "en") or "").strip().lower().replace("_", "-")
        if not (requested_language.startswith("en") or requested_language.startswith("fr")):
            raise ValidationError("Language must be English or French.")
        current["language"] = normalize_language(requested_language, default="en")

        try:
            current["meetingRetentionDays"] = _required_day_option(
                current.get("meetingRetentionDays"),
                _DATA_RETENTION_DAY_OPTIONS,
                "Meeting retention",
            )
            current["documentRetentionDays"] = _required_day_option(
                current.get("documentRetentionDays"),
                _DATA_RETENTION_DAY_OPTIONS,
                "Document retention",
            )
            current["shareDefaultExpirationDays"] = _required_day_option(
                current.get("shareDefaultExpirationDays"),
                _SHARE_EXPIRATION_DAY_OPTIONS,
                "Share-link expiration",
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        for key in (
            "shareRequirePassword",
            "shareAllowDownload",
            "shareIncludeScorecard",
            "meetingExtractActionItems",
            "meetingGenerateScorecard",
        ):
            current[key] = _normalized_boolean(current.get(key))

        summary_detail = str(current.get("meetingSummaryDetail") or "").strip().lower()
        if summary_detail not in _MEETING_SUMMARY_DETAIL_OPTIONS:
            raise ValidationError("Meeting summary detail must be brief, standard, or detailed.")
        current["meetingSummaryDetail"] = summary_detail

        persisted_settings = dict(current)
        persisted_settings.pop("aiModelPreset", None)

        try:
            self.repository.update_settings(user_id, persisted_settings)
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise ResourceNotFoundError("User not found.") from exc

            raise DatabaseError("Failed to update settings.") from exc

        return current


def _normalized_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalized_day_option(value: Any, allowed: set[int], *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized in allowed else default


def _required_day_option(value: Any, allowed: set[int], label: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be one of the available options.") from exc
    if normalized not in allowed:
        raise ValueError(f"{label} must be one of the available options.")
    return normalized


def _normalize_assistant_context(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    normalized = default_assistant_context()

    for destination, source_names in _ASSISTANT_CONTEXT_ALIASES.items():
        for source_name in source_names:
            if source_name in raw:
                normalized[destination] = raw[source_name]
                break

    enabled = normalized.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"false", "0", "no", "off"}
    normalized["enabled"] = bool(enabled)

    for key in normalized:
        if key == "enabled":
            continue
        normalized[key] = str(normalized.get(key) or "").strip()

    # Make previously saved reusable values visible in the new form without
    # treating old application-specific company or audience values as profile data.
    if not normalized.get("current_role"):
        normalized["current_role"] = normalized.get("role", "")
    if not normalized.get("industries"):
        normalized["industries"] = normalized.get("domain", "")
    if not normalized.get("career_goals"):
        normalized["career_goals"] = normalized.get("objective", "")

    # Keep older services useful while they are migrated to the explicit Career
    # Profile field names. Application-specific company and job values are never
    # inferred here.
    if not normalized.get("role"):
        normalized["role"] = normalized.get("current_role") or normalized.get("preferred_roles", "")
    if not normalized.get("domain"):
        normalized["domain"] = normalized.get("industries", "")
    if not normalized.get("objective"):
        normalized["objective"] = normalized.get("career_goals", "")

    if "response_mode" in normalized:
        normalized["response_mode"] = _normalize_ai_coaching_response_mode(
            normalized.get("response_mode")
        )

    return normalized


def _normalize_scorecard_source(
    value: Any,
    *,
    default: str | None = None,
) -> str:
    """Normalize current and legacy scorecard-source representations."""
    if isinstance(value, bool):
        return "microphone" if value else "speaker"

    normalized = " ".join(
        str(value or default or "").strip().lower().replace("_", "-").split("-")
    )
    normalized = " ".join(normalized.split())

    resolved = _SCORECARD_SOURCE_ALIASES.get(normalized)
    if resolved is not None:
        return resolved
    if default is not None:
        return default
    raise ValueError("Unsupported scorecard source.")
