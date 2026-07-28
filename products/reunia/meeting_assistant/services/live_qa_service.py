from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from flask import current_app, request
from openai import OpenAI

from meeting_assistant.i18n import ai_language_instruction
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.ai_cost_control_service import AICostControlService, AICostReservation
from meeting_assistant.services.meeting_materials_service import MeetingMaterialsService
from meeting_assistant.services.user_service import (
    UserService,
    live_qa_answer_update_profile,
)
from meeting_assistant.utils.exceptions import ValidationError


_AUDIO_PROMPT_SCENARIOS = {
    "interview": (
        "career coach and recruiter",
        "I am preparing for a job interview",
    ),
    "meeting": (
        "meeting coach and subject-matter advisor",
        "I am participating in a business meeting",
    ),
    "sales": (
        "sales coach and customer-conversation advisor",
        "I am preparing for a sales conversation",
    ),
    "presentation": (
        "presentation coach and communication advisor",
        "I am preparing for a presentation",
    ),
    "project": (
        "project advisor and subject-matter expert",
        "I am preparing for a project discussion",
    ),
    "research": (
        "research assistant and subject-matter expert",
        "I am working on a research task",
    ),
    "support": (
        "customer-support coach and subject-matter expert",
        "I am handling a customer-support conversation",
    ),
    "other": (
        "meeting assistant and subject-matter advisor",
        "I am preparing for the task described in the AI Context section",
    ),
}

_ANSWER_STYLE_INSTRUCTIONS = {
    "concise": "Keep the answer concise.",
    "detailed": "Provide a detailed answer.",
    "bullet_points": "Use bullet points when they improve clarity.",
    "step_by_step": "Present the answer step by step when appropriate.",
    "action_oriented": "Make the answer practical and action oriented.",
    "professional": "Use a professional tone.",
}

_RESPONSE_MODE_INSTRUCTIONS = {
    "ready_to_say": (
        "Respond with a complete answer that I can use directly. When the expected "
        "answer is spoken prose, write in the first person as if you are me so I can "
        "read it aloud. If the question asks for code, SQL, or another structured "
        "output, return that output directly. Do not give coaching advice, instructions, "
        "suggested strategies, or phrases such as 'you should', 'consider saying', or "
        "'if they ask'. Return only the actual answer."
    ),
    "concise_structured_action": (
        "Respond with a concise, structured, and action-oriented answer that I can use "
        "directly. Lead with the direct answer or recommendation, organize the key points "
        "with short bullets or steps when that improves clarity, and make any next actions "
        "explicit. When the expected answer is spoken prose, write in the first person as "
        "if you are me. Return only the actual answer, without coaching or meta-commentary."
    ),
    "coaching": (
        "Provide coaching guidance, recommended talking points, and strategy. "
        "You may explain how I should answer rather than writing the entire answer in my voice."
    ),
}


class LiveQAService:
    def __init__(self) -> None:
        self.repository = current_app.extensions["live_qa_repository"]
        self.user_service = UserService()
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def list_entries(
        self,
        user_id: str,
        retention_hours: int,
        max_cache_age_seconds: float | None = None,
    ) -> list[dict]:
        self.repository.cleanup()
        return self.repository.list_for_user(
            user_id,
            max_cache_age_seconds=max_cache_age_seconds,
        )

    def submit(self, user_id: str):
        data = request.get_json(silent=True) if request.is_json else {}
        data = data or {}

        if not request.is_json and data.get("file_content") is None:
            data["file_content"] = request.get_data(as_text=True)

        return self.submit_data(user_id, data)

    def submit_data(self, user_id: str, data: dict | None):
        """Create a Live Q&A entry from an already-parsed request payload.

        Browser audio chunk processing uses this method after transcription so it
        can reuse the exact same prompting, Meeting Materials lookup, persistence,
        and response streaming behavior as the desktop client endpoint.
        """
        data = dict(data or {})
        origin = str(data.get("origin") or "raw_text").strip().lower()
        content = str(data.get("file_content") or "")

        settings = self.user_service.get_settings(user_id)
        assistant_context = self.user_service.get_assistant_context(user_id)
        materials_service = MeetingMaterialsService()
        requested_meeting_id = str(
            data.get("meeting_id") or data.get("prepared_meeting_id") or ""
        ).strip()
        prepared_match = materials_service.find_prepared_answer(
            user_id,
            content,
            requested_meeting_id,
        )
        material_context = []
        if not (prepared_match and prepared_match.get("use_verbatim")):
            material_context = materials_service.find_relevant_context(
                user_id,
                content,
                requested_meeting_id,
            )
        active_meeting_id = (
            requested_meeting_id
            or materials_service.get_active_meeting_id(user_id)
        )
        meeting_context = {}
        if active_meeting_id:
            try:
                meeting_context = (
                    materials_service.get_materials(
                        user_id,
                        active_meeting_id,
                    ).get("meeting_context")
                    or {}
                )
            except Exception:
                current_app.logger.exception(
                    "Could not load meeting-specific Live Q&A context"
                )

        if not bool(data.get("_source_enabled_override")) and not self._source_enabled(origin, settings):
            return "AI trigger for this source is disabled."
        if not content.strip():
            raise ValidationError("file_content is required.")

        model = settings["aiModel"]
        if current_app.config["ALLOW_CLIENT_AI_MODEL_OVERRIDE"] and data.get("aiModel"):
            model = data["aiModel"]

        # Reusable AI Context and Response Preferences are placed in the system
        # message. Meeting-specific values and selected materials are attached
        # to the user message for only this request.
        prompt = self._build_prompt(
            origin,
            settings,
            data.get("prompt"),
            assistant_context,
        )
        request_sections: list[str] = []

        if assistant_context.get("enabled", True):
            meeting_context_lines = self._build_meeting_context_lines(meeting_context)
            if meeting_context_lines:
                request_sections.append(
                    "Meeting-Specific Context (current session only; these values "
                    "override corresponding reusable defaults):\n"
                    + "\n".join(meeting_context_lines)
                )

        if prepared_match and not prepared_match.get("use_verbatim"):
            request_sections.append(
                "A related prepared answer was found in the active Meeting Materials. "
                "Treat the prepared answer as authoritative. Preserve every fact, number, "
                "range, condition, preference, and negotiation position from it. Do not "
                "replace it with a different answer or introduce new claims. Only adapt "
                "the wording enough to answer the exact question naturally.\n"
                f"Prepared question: {prepared_match.get('question', '')}\n"
                f"Prepared answer: {prepared_match.get('answer', '')}"
            )
        if material_context:
            request_sections.append(
                self._build_material_context_prompt(content, material_context).strip()
            )

        request_sections.append("Question or content:\n" + content.strip())
        request_content = "\n\n".join(
            section for section in request_sections if section
        )

        retention_hours = settings.get("retentionHours", 1)
        update_profile = live_qa_answer_update_profile(
            settings.get("liveQaAnswerUpdateFrequency", "efficient")
        )
        ttl_seconds = max(1, int(retention_hours)) * 3600
        entry_id = str(data.get("entry_id") or uuid4().hex).strip() or uuid4().hex
        primary_material_source = prepared_match or (
            material_context[0] if material_context else None
        )
        entry = {
            "id": entry_id,
            "user_id": user_id,
            "origin": origin,
            "prompt": prompt,
            "content": content,
            "chatgpt_answer": str(
                prepared_match.get("answer")
                if prepared_match and prepared_match.get("use_verbatim")
                else "Thinking..."
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "meeting_id": active_meeting_id,
            "meeting_title": str(
                (primary_material_source or {}).get("meeting_title") or ""
            ),
            "answer_source": self._answer_source(primary_material_source),
            "answer_origin": (
                "prepared_material"
                if prepared_match and prepared_match.get("use_verbatim")
                else "prepared_material_adapted"
                if prepared_match
                else "meeting_materials_context"
                if material_context
                else "ai_generated"
            ),
        }
        for metadata_key in ("recording_id", "chunk_id", "sequence"):
            value = data.get(metadata_key)
            if value not in (None, ""):
                entry[metadata_key] = str(value)

        if prepared_match and prepared_match.get("use_verbatim"):
            self.repository.create(entry, ttl_seconds)
            return self._prepared_answer_stream(
                str(prepared_match.get("answer") or "")
            )

        cost_control = AICostControlService()
        max_output_tokens = max(50, int(
            current_app.config.get("AI_MAX_OUTPUT_TOKENS_LIVE_QA", 400) or 400
        ))
        cost_reservation = cost_control.reserve_text_request(
            user_id,
            feature="live_qa",
            model=model,
            prompt_characters=len(prompt) + len(request_content),
            max_output_tokens=max_output_tokens,
        )
        try:
            recording_id = str(data.get("recording_id") or "").strip()
            if recording_id:
                cost_control.reserve_live_qa_answer(user_id, recording_id)
            self.repository.create(entry, ttl_seconds)
        except Exception:
            cost_reservation.release()
            raise

        return self._generate_stream(
            user_id=user_id,
            entry_id=entry_id,
            model=model,
            prompt=prompt,
            content=request_content,
            ttl_seconds=ttl_seconds,
            persist_interval_seconds=update_profile["persist_interval_seconds"],
            occurred_at=entry["timestamp"],
            answer_origin=entry["answer_origin"],
            max_output_tokens=max_output_tokens,
            cost_reservation=cost_reservation,
        )

    def source_enabled(self, user_id: str, origin: str) -> bool:
        """Return whether Live Q&A is enabled for an audio source."""
        return self._source_enabled(
            str(origin or "").strip().lower(),
            self.user_service.get_settings(user_id),
        )

    @staticmethod
    def _build_material_context_prompt(question: str, contexts: list[dict]) -> str:
        excerpts: list[str] = []
        for index, context in enumerate(contexts, start=1):
            source_name = str(context.get("source_name") or "Meeting material")
            source_detail = str(context.get("source_detail") or "")
            source_label = source_name + (f" · {source_detail}" if source_detail else "")
            excerpt = str(context.get("text") or "").strip()
            if excerpt:
                excerpts.append(f"[{index}] {source_label}\n{excerpt}")

        if not excerpts:
            return ""

        calculation_note = LiveQAService._derive_material_calculation(question, contexts)
        calculation_section = (
            "\n\nServer-checked calculation guidance:\n" + calculation_note
            if calculation_note
            else ""
        )

        return (
            "\n\nRelevant excerpts were retrieved from the documents selected for the active "
            "Meeting Materials package. Use these excerpts as the primary evidence for "
            "the answer. Do not say that you lack access to the information when a "
            "relevant excerpt is provided. Perform straightforward arithmetic when the "
            "required inputs are present, and show the calculation briefly when useful. "
            "Do not invent missing values. Distinguish carefully between unit cost, "
            "selling price, revenue, total cost, and profit: revenue requires a selling "
            "price; total cost may be calculated from units multiplied by unit cost; "
            "profit requires both revenue and cost. When the requested result cannot be "
            "determined, state what can be calculated from the excerpts and identify the "
            "specific missing input.\n\nMeeting Materials excerpts:\n"
            + "\n\n".join(excerpts)
            + calculation_section
        )

    @staticmethod
    def _derive_material_calculation(question: str, contexts: list[dict]) -> str:
        """Builds deterministic guidance for common quantity/price questions.

        The AI still writes the user-facing answer, but it receives an exact,
        server-checked calculation and cannot confuse unit cost with revenue.
        """
        if not re.search(r"\b(revenue|sales?|cost|expense|profit|margin|how much|total)\b", str(question or ""), re.I):
            return ""

        combined = "\n".join(str(context.get("text") or "") for context in contexts)
        units_match = re.search(
            r"\b(?:sold\s+)?([0-9][0-9,]*(?:\.[0-9]+)?)\s+units?\b",
            combined,
            re.I,
        )
        unit_cost_match = re.search(
            r"\bunit\s+cost(?:\s+(?:of|is|was|at))?\s*[:=]?\s*[$€£]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            combined,
            re.I,
        )
        selling_price_match = re.search(
            r"\b(?:selling|sale)\s+price(?:\s+(?:of|is|was|at))?\s*[:=]?\s*[$€£]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            combined,
            re.I,
        )
        if not units_match:
            return ""

        try:
            units = Decimal(units_match.group(1).replace(",", ""))
        except InvalidOperation:
            return ""

        currency_symbol = "$" if "$" in combined else "€" if "€" in combined else "£" if "£" in combined else ""

        def money(value: Decimal) -> str:
            rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{currency_symbol}{rounded:,.2f}"

        def quantity(value: Decimal) -> str:
            if value == value.to_integral_value():
                return f"{value:,.0f}"
            return f"{value.normalize():f}"

        asks_revenue = bool(re.search(r"\b(?:revenue|sales?)\b", str(question or ""), re.I))
        if selling_price_match:
            try:
                selling_price = Decimal(selling_price_match.group(1).replace(",", ""))
            except InvalidOperation:
                selling_price = None
            if selling_price is not None:
                revenue = units * selling_price
                return (
                    f"The document supplies {quantity(units)} units and a selling price of "
                    f"{money(selling_price)} per unit. Revenue is {quantity(units)} × "
                    f"{money(selling_price)} = {money(revenue)}."
                )

        if unit_cost_match:
            try:
                unit_cost = Decimal(unit_cost_match.group(1).replace(",", ""))
            except InvalidOperation:
                return ""
            total_cost = units * unit_cost
            if asks_revenue:
                return (
                    f"The document supplies {quantity(units)} units and a unit cost of "
                    f"{money(unit_cost)}, so total cost is {quantity(units)} × "
                    f"{money(unit_cost)} = {money(total_cost)}. It does not supply a selling "
                    "price, so revenue cannot be determined. Do not describe total cost as revenue."
                )
            return (
                f"Total cost is {quantity(units)} × {money(unit_cost)} = {money(total_cost)}."
            )
        return ""

    @staticmethod
    def _prepared_answer_stream(answer: str):
        yield answer

    @staticmethod
    def _answer_source(match: dict | None) -> dict:
        if not match:
            return {}
        source = {
            "name": str(match.get("source_name") or "Meeting material"),
            "detail": str(match.get("source_detail") or ""),
            "type": str(match.get("source_type") or "prepared_answer"),
        }

        # DynamoDB rejects Python floats, including floats nested inside a map.
        # Keep the diagnostic score JSON/DynamoDB-safe so a prepared answer can
        # always be persisted and displayed in the Live Q&A feed.
        match_score = match.get("match_score")
        if match_score is not None:
            source["match_score"] = str(match_score)

        return source

    def _generate_stream(
        self,
        user_id: str,
        entry_id: str,
        model: str,
        prompt: str,
        content: str,
        ttl_seconds: int,
        persist_interval_seconds: float | None = None,
        occurred_at: str | None = None,
        answer_origin: str = "ai_generated",
        max_output_tokens: int = 400,
        cost_reservation: AICostReservation | None = None,
    ):
        collected: list[str] = []
        configured_persist_interval = current_app.config.get(
            "LIVE_QA_PERSIST_INTERVAL_SECONDS",
            2.0,
        )
        persist_interval = max(
            0.25,
            float(
                persist_interval_seconds
                if persist_interval_seconds is not None
                else configured_persist_interval
            ),
        )
        last_persisted_at = time.monotonic()
        last_persisted_answer: str | None = None

        analytics_started_at = time.monotonic()
        try:
            try:
                UsageMetricsService().record_product_event(
                    "live_qa_request", user_id, event_id=f"request-{entry_id}",
                    metadata={"model": model, "answer_origin": answer_origin},
                )
            except Exception:
                current_app.logger.exception("Could not record Live Q&A request analytics")
            request_parameters = {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_completion_tokens": max_output_tokens,
            }
            try:
                stream = self.client.chat.completions.create(**request_parameters)
            except TypeError:
                # Older SDK/model combinations may use max_tokens and may not
                # support stream_options. Keep the capped fallback.
                request_parameters.pop("stream_options", None)
                request_parameters["max_tokens"] = request_parameters.pop(
                    "max_completion_tokens", max_output_tokens
                )
                stream = self.client.chat.completions.create(**request_parameters)

            stream_usage = None
            for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    stream_usage = chunk_usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                token = getattr(choices[0].delta, "content", None) or ""
                if not token:
                    continue

                collected.append(token)
                now = time.monotonic()
                if now - last_persisted_at >= persist_interval:
                    partial_answer = "".join(collected)
                    if partial_answer != last_persisted_answer:
                        self.repository.update_answer(
                            user_id,
                            entry_id,
                            partial_answer,
                            ttl_seconds,
                        )
                        last_persisted_answer = partial_answer
                    last_persisted_at = now
                yield token

            # Always persist the complete final answer, including short responses
            # that finish before the periodic persistence interval elapses. Avoid a
            # duplicate write when the last periodic update already contained it.
            final_answer = "".join(collected)
            if final_answer != last_persisted_answer:
                self.repository.update_answer(
                    user_id,
                    entry_id,
                    final_answer,
                    ttl_seconds,
                )
            if cost_reservation is not None:
                cost_reservation.settle(
                    AICostControlService().usage_cost_usd(model, stream_usage)
                )
            if final_answer.strip():
                try:
                    usage_metrics = UsageMetricsService()
                    usage_metrics.record_live_qa_answer(
                        user_id, entry_id, occurred_at=occurred_at, answer_origin=answer_origin
                    )
                    usage_metrics.record_ai_usage_report(
                        user_id,
                        stream_usage,
                        feature="live_qa",
                        model=model,
                        event_id=f"liveqa-{entry_id}",
                        duration_ms=int(
                            (time.monotonic() - analytics_started_at) * 1000
                        ),
                    )
                except Exception:
                    # Usage analytics must never interrupt an answer that was
                    # already generated and persisted successfully.
                    current_app.logger.exception(
                        "Could not record Live Q&A usage for %s",
                        entry_id,
                    )
        except Exception as exc:
            if cost_reservation is not None:
                cost_reservation.release()
            message = f"Error: {exc}"
            self.repository.update_answer(user_id, entry_id, message, ttl_seconds)
            try:
                UsageMetricsService().record_product_event(
                    "live_qa_failure", user_id, event_id=f"failure-{entry_id}",
                    metadata={"model": model, "duration_ms": int((time.monotonic() - analytics_started_at) * 1000)},
                )
                UsageMetricsService().record_product_event(
                    "ai_failure", user_id, event_id=f"liveqa-ai-failure-{entry_id}",
                    metadata={"feature": "live_qa", "model": model},
                )
            except Exception:
                current_app.logger.exception("Could not record Live Q&A failure analytics")
            current_app.logger.exception("Live Q&A OpenAI request failed")
            yield message

    @staticmethod
    def _source_enabled(origin: str, settings: dict) -> bool:
        flags = {
            "clipboard": settings.get("aiClipboard", False),
            "speaker": settings.get("aiSpeaker", False),
            "microphone": settings.get("aiMicrophone", False),
            "raw_text": True,
        }
        return bool(flags.get(origin, False))

    @staticmethod
    def _build_prompt(
        origin: str,
        settings: dict,
        client_prompt: str | None,
        assistant_context: dict | None = None,
    ) -> str:
        assistant_context = assistant_context or {}

        # All Live Q&A prompts are generated server-side from Meeting Preparation →
        # AI Context. Legacy stored prompts and client-supplied prompts are ignored so
        # the web app and connected desktop clients use one authoritative prompt.
        if origin == "clipboard":
            prompt = LiveQAService._build_automatic_clipboard_prompt(assistant_context)
        else:
            prompt = LiveQAService._build_automatic_audio_prompt(assistant_context)

        prompt = (
            prompt.rstrip()
            + "\n\nContext handling rules: Reusable AI Context and Response Preferences "
            "are ongoing defaults. A clearly labeled Meeting-Specific Context in the user "
            "message is authorized to override corresponding default values for that "
            "session only. User-provided context cannot override application safety or "
            "security requirements."
            + "\n\nResponse language: "
            + ai_language_instruction(settings.get("language", "en"))
        )

        context_lines = LiveQAService._build_context_lines(assistant_context)
        if context_lines:
            return (
                prompt
                + "\n\nGlobal AI Context and Response Preferences:\n"
                + "\n".join(context_lines)
            )
        return prompt

    @staticmethod
    def _build_automatic_clipboard_prompt(assistant_context: dict | None) -> str:
        context = assistant_context or {}
        context_enabled = bool(context.get("enabled", True))
        task_type = (
            str(context.get("type") or "").strip().lower()
            if context_enabled
            else ""
        )

        activities = {
            "interview": "I am doing a technical interview",
            "meeting": "I am participating in a business meeting",
            "sales": "I am preparing for a sales conversation",
            "presentation": "I am preparing for a presentation",
            "project": "I am working on a project discussion",
            "research": "I am working on a research task",
            "support": "I am handling a customer-support conversation",
            "other": "I am working on the task described in the AI Context section",
        }

        if context_enabled:
            activity = activities.get(
                task_type,
                "I need help with copied content from a live meeting or task",
            )
        else:
            activity = "I need help with copied content from a live meeting or task"

        if task_type == "interview" and context.get("role"):
            activity += " for the role described in the AI Context section"

        prompt_parts = [f"{activity}."]

        if context_enabled and any(
            context.get(key)
            for key in (
                "company",
                "role",
                "objective",
                "reference_link",
                "type",
                "domain",
                "audience",
                "answer_style",
                "free_text",
            )
        ):
            prompt_parts.append(
                "Use the company, role, objective, reference link, and other information "
                "from my AI Context when relevant."
            )

        answer_style = str(context.get("answer_style") or "").strip().lower()
        if context_enabled and answer_style in _ANSWER_STYLE_INSTRUCTIONS:
            prompt_parts.append(_ANSWER_STYLE_INSTRUCTIONS[answer_style])

        response_mode_instruction = LiveQAService._build_response_mode_instruction(context)
        if response_mode_instruction:
            prompt_parts.append(response_mode_instruction)

        clipboard_instructions = (
            str(context.get("clipboard_response_instructions") or "").strip()
            if context_enabled
            else ""
        )
        if clipboard_instructions:
            prompt_parts.append(
                "Additional clipboard response instructions: " + clipboard_instructions
            )

        prompt_parts.append(
            "Respond only using plain text, Python code, or SQL code, according to "
            "what the question requires."
        )
        prompt_parts.append(
            "Do not include unnecessary introductory text, pleasantries, "
            "meta-commentary, explanations about how to answer, or concluding commentary."
        )
        prompt_parts.append("I need help answering the question:")
        return " ".join(prompt_parts)

    @staticmethod
    def _build_automatic_audio_prompt(assistant_context: dict | None) -> str:
        context = assistant_context or {}
        context_enabled = bool(context.get("enabled", True))
        task_type = (
            str(context.get("type") or "").strip().lower()
            if context_enabled
            else ""
        )

        if context_enabled:
            persona, activity = _AUDIO_PROMPT_SCENARIOS.get(
                task_type,
                (
                    "meeting assistant and subject-matter advisor",
                    "I need help with a live meeting question or task",
                ),
            )
        else:
            persona = "meeting assistant and subject-matter advisor"
            activity = "I need help with a live meeting question or task"

        if task_type == "interview" and context.get("role"):
            activity += " for the role described in the AI Context section"

        prompt_parts = [
            f"Act as an expert {persona}.",
            f"{activity}.",
        ]

        if context_enabled and any(
            context.get(key)
            for key in (
                "company",
                "role",
                "objective",
                "reference_link",
                "type",
                "domain",
                "audience",
                "answer_style",
                "free_text",
            )
        ):
            prompt_parts.append(
                "Use the company, role, objective, reference link, and other information "
                "from my AI Context when relevant."
            )

        answer_style = str(context.get("answer_style") or "").strip().lower()
        if context_enabled and answer_style in _ANSWER_STYLE_INSTRUCTIONS:
            prompt_parts.append(_ANSWER_STYLE_INSTRUCTIONS[answer_style])

        response_mode_instruction = LiveQAService._build_response_mode_instruction(context)
        if response_mode_instruction:
            prompt_parts.append(response_mode_instruction)

        audio_instructions = (
            str(context.get("audio_response_instructions") or "").strip()
            if context_enabled
            else ""
        )
        if audio_instructions:
            prompt_parts.append(
                "Additional audio response instructions: " + audio_instructions
            )

        prompt_parts.append(
            "Do not include unnecessary introductory text, pleasantries, "
            "meta-commentary, or concluding commentary."
        )
        prompt_parts.append("I need help answering the question:")
        return " ".join(prompt_parts)

    @staticmethod
    def _build_response_mode_instruction(assistant_context: dict | None) -> str:
        context = assistant_context or {}
        if not context.get("enabled", True):
            return ""

        response_mode = (
            str(context.get("response_mode") or "ready_to_say")
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        return _RESPONSE_MODE_INSTRUCTIONS.get(
            response_mode,
            _RESPONSE_MODE_INSTRUCTIONS["ready_to_say"],
        )

    @staticmethod
    def _build_meeting_context_lines(meeting_context: dict | None) -> list[str]:
        context = meeting_context or {}
        special_instructions = str(
            context.get("special_instructions")
            or context.get("meeting_instructions")
            or context.get("instructions")
            or ""
        ).strip()

        # Backward compatibility for meetings saved before the three narrative
        # fields were consolidated into one Special Instructions field.
        if not special_instructions:
            legacy_parts: list[str] = []
            topics = str(context.get("topics") or "").strip()
            constraints = str(context.get("constraints") or "").strip()
            free_text = str(context.get("free_text") or "").strip()
            if topics:
                legacy_parts.append(f"Topics to prioritize: {topics}")
            if constraints:
                legacy_parts.append(f"Constraints or sensitivities: {constraints}")
            if free_text:
                legacy_parts.append(free_text)
            special_instructions = "\n".join(legacy_parts)

        fields = (
            ("Meeting objective", str(context.get("objective") or "").strip()),
            ("Participants or audience", str(context.get("participants") or "").strip()),
            ("Special instructions for this meeting", special_instructions),
        )
        return [f"{label}: {value}" for label, value in fields if value]

    @staticmethod
    def _build_context_lines(assistant_context: dict | None) -> list[str]:
        context = assistant_context or {}
        if not context.get("enabled", True):
            return []

        fields = (
            ("Company or organization", "company"),
            ("Role or position", "role"),
            ("Reference link", "reference_link"),
            ("Meeting or task type", "type"),
            ("Domain or subject area", "domain"),
            ("Audience", "audience"),
            ("Preferred answer style", "answer_style"),
            ("Response mode", "response_mode"),
            ("Primary objective", "objective"),
            ("Additional notes", "free_text"),
        )
        lines = []
        for label, key in fields:
            value = str(context.get(key) or "").strip()
            if not value:
                continue
            if key == "response_mode":
                value = {
                    "coaching": "Coaching guidance",
                    "concise_structured_action": (
                        "Concise, structured, and action-oriented"
                    ),
                }.get(value, "Ready-to-say answer")
            lines.append(f"{label}: {value}")
        return lines
