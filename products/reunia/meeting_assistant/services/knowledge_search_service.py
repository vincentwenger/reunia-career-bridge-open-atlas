from __future__ import annotations

import time
import hashlib
import json

import re
from typing import Any

from flask import current_app
from openai import OpenAI

from meeting_assistant.i18n import ai_language_instruction, normalize_language
from meeting_assistant.services.knowledge_service import KnowledgeService
from meeting_assistant.services.application_materials_service import (
    ApplicationMaterialsService,
    _context_match_score,
    _entry_context_text,
    _extract_material_entries,
)
from meeting_assistant.services.transcript_service import TranscriptService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.ai_cost_control_service import (
    AICostControlService,
    raise_if_openai_limited,
)
from meeting_assistant.utils.exceptions import ExternalServiceError, ValidationError

_MAX_QUESTION_CHARACTERS = 4_000
_MAX_EVIDENCE_ITEMS = 5
_MAX_EVIDENCE_CHARACTERS = 12_000
_MAX_LIBRARY_FILES = 80
_MAX_MEETINGS = 120
_MAX_TEXT_CHUNK_CHARACTERS = 1_800

_SCOPE_ALIASES = {
    "current_meeting": "current_meeting",
    "library": "library",
    "meetings": "meetings",
    "all": "all",
    # Meeting Review keeps the selected completed meeting as its primary scope.
    "this_meeting": "meetings",
    "meeting_review_related": "meeting_review_related",
    # Legacy aliases retained for older browser bundles and saved requests.
    "this_meeting_and_files": "meeting_and_library",
    "all_meetings": "meetings",
}


class KnowledgeSearchService:
    """Answer questions using the user's selected documents and meeting history."""

    def __init__(
        self,
        *,
        client: OpenAI | None = None,
        knowledge_service: KnowledgeService | None = None,
        application_materials_service: ApplicationMaterialsService | None = None,
        transcript_service: TranscriptService | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self._client = client
        self.knowledge = knowledge_service or KnowledgeService()
        self.materials = application_materials_service or ApplicationMaterialsService()
        self.transcripts = transcript_service or TranscriptService()
        self.users = user_service or UserService()

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def answer(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("The request body must be a JSON object.")

        question = str(payload.get("question") or "").strip()
        if not question:
            raise ValidationError("A question is required.")
        if len(question) > _MAX_QUESTION_CHARACTERS:
            raise ValidationError(
                f"The question must be {_MAX_QUESTION_CHARACTERS:,} characters or fewer."
            )

        language = normalize_language(
            self.users.get_settings(user_id).get("language"),
            default="en",
        )

        requested_scope = str(
            payload.get("source_scope") or payload.get("search_scope") or "current_meeting"
        ).strip().lower()
        scope = _SCOPE_ALIASES.get(requested_scope)
        if not scope:
            raise ValidationError("Choose a valid Knowledge Search scope.")

        if requested_scope in {
            "this_meeting",
            "this_meeting_and_files",
            "meeting_review_related",
        } and not _requested_meeting_ids(payload):
            raise ValidationError("Select a meeting before asking a question about it.")

        evidence: list[dict[str, Any]] = []
        if scope == "meeting_review_related":
            evidence.extend(
                self._meeting_review_related_evidence(user_id, question, payload)
            )
        else:
            if scope in {"current_meeting", "all"}:
                evidence.extend(self._current_meeting_evidence(user_id, question, payload))
            if scope in {"library", "all", "meeting_and_library"}:
                evidence.extend(self._library_evidence(user_id, question, payload))
            if scope in {"meetings", "all", "meeting_and_library"}:
                evidence.extend(self._meeting_evidence(user_id, question, payload))

        selected = self._select_evidence(question, evidence)
        if not selected:
            return {
                "answer": (
                    "Je n’ai trouvé aucune information pertinente dans les sources "
                    "sélectionnées. Essayez une autre portée, sélectionnez une réunion "
                    "ou une collection, ou importez un document contenant la réponse."
                    if language == "fr"
                    else "I couldn't find relevant information in the selected knowledge "
                    "sources. Try another scope, select a meeting or collection, or "
                    "upload a document that contains the answer."
                ),
                "sources": [],
            }

        model = self._model_for_user(user_id)
        system_message = (
            self._system_message(payload)
            + "\n\n"
            + ai_language_instruction(language)
        )
        user_message = self._user_message(question, selected, payload)

        sources = _unique_sources(selected)
        cache = current_app.extensions.get("ai_response_cache")
        cache_key = "knowledge:" + hashlib.sha256(
            json.dumps(
                {
                    "user": str(user_id),
                    "model": model,
                    "system": system_message,
                    "user_message": user_message,
                    "sources": sources,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        cached = cache.get(cache_key) if cache is not None else None
        if isinstance(cached, dict) and str(cached.get("answer") or "").strip():
            return {"answer": str(cached["answer"]), "sources": cached.get("sources") or sources}

        max_output_tokens = max(100, int(
            current_app.config.get("AI_MAX_OUTPUT_TOKENS_KNOWLEDGE_SEARCH", 700) or 700
        ))
        cost_control = AICostControlService()
        reservation = cost_control.reserve_text_request(
            user_id,
            feature="knowledge_search",
            model=model,
            prompt_characters=len(system_message) + len(user_message),
            max_output_tokens=max_output_tokens,
        )
        started_at = time.monotonic()
        try:
            request_parameters = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
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
            reservation.settle(cost_control.usage_cost_usd(model, getattr(response, "usage", None)))
            answer = str(response.choices[0].message.content or "").strip()
            try:
                UsageMetricsService().record_ai_response(
                    user_id, response, feature="knowledge_search", model=model,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
            except Exception:
                current_app.logger.exception("Could not record Knowledge Search AI usage")
        except Exception as exc:
            reservation.release()
            raise_if_openai_limited(exc)
            try:
                UsageMetricsService().record_product_event(
                    "ai_failure", user_id,
                    metadata={"feature": "knowledge_search", "model": model, "duration_ms": int((time.monotonic() - started_at) * 1000)},
                )
            except Exception:
                current_app.logger.exception("Could not record Knowledge Search AI failure")
            current_app.logger.exception("Knowledge Search OpenAI request failed")
            raise ExternalServiceError(
                "The AI could not answer the question right now. Please try again."
            ) from exc

        if not answer:
            answer = (
                "L’IA n’a renvoyé aucune réponse."
                if language == "fr"
                else "No answer was returned from the AI."
            )

        result = {"answer": answer, "sources": sources}
        if cache is not None:
            cache.set(
                cache_key,
                result,
                max(60, int(current_app.config.get("AI_RESPONSE_CACHE_SECONDS", 3600) or 3600)),
            )
        return result

    def _meeting_review_related_evidence(
        self,
        user_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Search the selected meeting first, then add related knowledge.

        Meeting Review remains centered on one completed meeting. Related
        documents and previous meetings are optional supporting context.
        """
        primary_ids = _requested_meeting_ids(payload)
        primary_evidence = self._meeting_evidence(user_id, question, payload)
        if not primary_evidence:
            return []

        for item in primary_evidence:
            item["score"] = float(item.get("score") or 0.0) + 0.35
            source = item.get("source")
            if isinstance(source, dict):
                source["is_primary_meeting"] = True

        related_payload = dict(payload)
        related_payload.pop("meeting_id", None)
        related_payload.pop("meeting_ids", None)

        related_evidence = self._library_evidence(user_id, question, related_payload)
        for item in related_evidence:
            item["score"] = float(item.get("score") or 0.0) * 0.9

        for item in self._meeting_evidence(user_id, question, related_payload):
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            source_ids = {
                _first_text(source.get("meeting_id")),
                _first_text(source.get("date")),
                _first_text(source.get("meeting_date")),
            }
            if primary_ids & {value for value in source_ids if value}:
                continue
            item["score"] = float(item.get("score") or 0.0) * 0.9
            related_evidence.append(item)

        return primary_evidence + related_evidence

    def _current_meeting_evidence(
        self,
        user_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        meeting_id = str(payload.get("meeting_package_id") or "").strip()
        selected_id, meeting = self.materials._resolve_application(user_id, meeting_id)
        if not meeting:
            return []

        meeting_title = str(meeting.get("application_title") or "Current application")
        entries = self.materials._ensure_material_index(user_id, meeting)
        evidence: list[dict[str, Any]] = []
        for entry in entries:
            text = _entry_context_text(entry)
            if not text:
                continue
            source_name = str(entry.get("source_name") or "Meeting material")
            source_detail = str(entry.get("source_detail") or "")
            evidence.append(
                {
                    "text": text,
                    "score": _context_match_score(question, entry),
                    "source": {
                        "source_type": "Current Meeting Material",
                        "type": "Current Meeting Material",
                        "title": source_name,
                        "filename": source_name,
                        "section": source_detail,
                        "meeting_name": meeting_title,
                        "meeting_id": selected_id,
                    },
                }
            )

        meeting_context = meeting.get("application_context")
        if isinstance(meeting_context, dict):
            context_text = _mapping_text(meeting_context)
            if context_text:
                evidence.append(
                    {
                        "text": context_text,
                        "score": _simple_match_score(question, context_text),
                        "source": {
                            "source_type": "Meeting Context",
                            "type": "Meeting Context",
                            "title": meeting_title,
                            "meeting_name": meeting_title,
                            "meeting_id": selected_id,
                            "section": "Meeting-specific context",
                        },
                    }
                )
        return evidence

    def _library_evidence(
        self,
        user_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        library = self.knowledge.list_library(user_id)
        collection_ids = {
            str(value).strip()
            for value in _string_list(payload.get("collection_ids"))
            if str(value).strip()
        }
        requested_file_ids = {
            str(value).strip()
            for value in _string_list(payload.get("library_file_ids"))
            if str(value).strip()
        }

        files = []
        for item in library.get("files", []):
            if collection_ids and str(item.get("collection_id") or "") not in collection_ids:
                continue
            # A file list sent by Current Meeting is only a narrowing hint. For the
            # dedicated Library and All scopes, the selected collection remains the
            # authoritative filter.
            if requested_file_ids and str(payload.get("source_scope") or "") == "current_meeting":
                if str(item.get("file_id") or "") not in requested_file_ids:
                    continue
            files.append(item)
            if len(files) >= _MAX_LIBRARY_FILES:
                break

        evidence: list[dict[str, Any]] = []
        for file_item in files:
            file_id = str(file_item.get("file_id") or "")
            if not file_id:
                continue
            try:
                raw_item, content = self.knowledge.get_file(user_id, file_id)
                entries = _extract_material_entries(raw_item, content)
            except Exception:
                current_app.logger.exception(
                    "Knowledge Search could not read document %s", file_id
                )
                continue

            for entry in entries:
                text = _entry_context_text(entry)
                if not text:
                    continue
                source_detail = str(entry.get("source_detail") or "")
                evidence.append(
                    {
                        "text": text,
                        "score": _context_match_score(question, entry),
                        "source": {
                            "source_type": "Document",
                            "type": "Document",
                            "filename": str(
                                file_item.get("display_name")
                                or file_item.get("filename")
                                or "Document"
                            ),
                            "display_name": str(
                                file_item.get("display_name")
                                or file_item.get("filename")
                                or "Document"
                            ),
                            "file_id": file_id,
                            "collection_name": str(
                                file_item.get("collection_name") or "Uncategorized"
                            ),
                            "collection_id": str(file_item.get("collection_id") or ""),
                            "section": source_detail,
                        },
                    }
                )
        return evidence

    def _meeting_evidence(
        self,
        user_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        requested_ids = {
            str(value).strip()
            for value in _string_list(payload.get("meeting_ids"))
            if str(value).strip()
        }
        direct_meeting_id = str(payload.get("meeting_id") or "").strip()
        if direct_meeting_id:
            requested_ids.add(direct_meeting_id)

        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        date_from = str(filters.get("date_from") or "").strip()
        date_to = str(filters.get("date_to") or "").strip()
        participant = str(filters.get("participant") or "").strip().casefold()
        content_type = str(filters.get("content_type") or "all").strip().lower()
        if content_type not in {
            "all",
            "transcript",
            "summary",
            "decisions",
            "actions",
            "questions",
        }:
            content_type = "all"

        evidence: list[dict[str, Any]] = []
        meetings = self.transcripts.list_for_user(user_id)[:_MAX_MEETINGS]
        for index, meeting in enumerate(meetings):
            meeting_id = _first_text(
                meeting.get("meeting_id"),
                meeting.get("transcript_id"),
                meeting.get("id"),
            )
            timestamp = _first_text(meeting.get("timestamp"), meeting.get("date"))
            if requested_ids and meeting_id not in requested_ids and timestamp not in requested_ids:
                continue
            date_value = timestamp[:10]
            if date_from and date_value and date_value < date_from:
                continue
            if date_to and date_value and date_value > date_to:
                continue

            participants = _string_list(
                meeting.get("participants") or meeting.get("prepared_meeting_participants")
            )
            searchable_participants = " ".join(participants).casefold()
            if participant and participant not in searchable_participants:
                # Some transcript formats only mention participants in the body.
                transcript_for_participant = _first_text(
                    meeting.get("transcript"), meeting.get("raw_transcript")
                ).casefold()
                if participant not in transcript_for_participant:
                    continue

            title = _first_text(meeting.get("meeting_name"), meeting.get("title"))
            if not title:
                title = f"Meeting {index + 1}"

            sections: list[tuple[str, str]] = []
            if content_type in {"all", "summary", "decisions"}:
                summary = _first_text(meeting.get("summary"))
                if summary:
                    sections.append(("Summary", summary))
            if content_type in {"all", "decisions"}:
                decisions = _searchable_items(
                    meeting.get("decisions") or meeting.get("key_decisions")
                )
                if decisions:
                    sections.append(
                        ("Decisions", "\n".join(f"- {item}" for item in decisions))
                    )
            if content_type in {"all", "actions"}:
                actions = _searchable_items(meeting.get("action_items"))
                if actions:
                    sections.append(("Action items", "\n".join(f"- {item}" for item in actions)))
            if content_type in {"all", "questions"}:
                questions = _searchable_items(meeting.get("open_questions"))
                if questions:
                    sections.append(("Open questions", "\n".join(f"- {item}" for item in questions)))
            if content_type == "all":
                wins = _searchable_items(meeting.get("key_wins"))
                if wins:
                    sections.append(("Key wins", "\n".join(f"- {item}" for item in wins)))

                improvements = _searchable_items(meeting.get("improvement_areas"))
                if improvements:
                    sections.append(
                        ("Improvement areas", "\n".join(f"- {item}" for item in improvements))
                    )

                scorecard = {
                    "final_score": meeting.get("final_grade")
                    or meeting.get("final_weighted_grade")
                    or meeting.get("overall_score"),
                    "content_average_score": meeting.get("content_average_score"),
                    "form_average_score": meeting.get("form_average_score"),
                    "scorecard_source": meeting.get("scorecard_source"),
                }
                form_metrics = meeting.get("form_metrics")
                if isinstance(form_metrics, dict):
                    scorecard["form_metrics"] = form_metrics
                scorecard_text = _mapping_text(scorecard)
                if scorecard_text:
                    sections.append(("Scorecard and communication analysis", scorecard_text))

                content_grades = meeting.get("content_grades")
                if isinstance(content_grades, list):
                    answer_analysis = "\n\n".join(
                        _mapping_text(item)
                        for item in content_grades
                        if isinstance(item, dict) and _mapping_text(item)
                    )
                    if answer_analysis:
                        sections.append(("Answer analysis", answer_analysis))
            if content_type in {"all", "transcript", "decisions"}:
                transcript = _first_text(meeting.get("transcript"), meeting.get("raw_transcript"))
                for part_number, chunk in enumerate(_chunk_text(transcript), start=1):
                    section = "Transcript" if part_number == 1 else f"Transcript · part {part_number}"
                    sections.append((section, chunk))

            for section, text in sections:
                evidence.append(
                    {
                        "text": text,
                        "score": _simple_match_score(question, f"{title} {section} {text}"),
                        "source": {
                            "source_type": "Previous Meeting",
                            "type": "Previous Meeting",
                            "meeting_name": title,
                            "title": title,
                            "meeting_id": meeting_id,
                            "date": timestamp,
                            "meeting_date": timestamp,
                            "section": section,
                        },
                    }
                )
        return evidence

    @staticmethod
    def _select_evidence(
        question: str,
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not evidence:
            return []

        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            text = str(item.get("text") or "").strip()
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            if not text:
                continue
            source_key = _first_text(
                source.get("file_id"),
                source.get("meeting_id"),
                source.get("filename"),
                source.get("title"),
            )
            key = (source_key, re.sub(r"\s+", " ", text).casefold()[:500])
            if key in seen:
                continue
            seen.add(key)
            item["score"] = max(
                float(item.get("score") or 0.0),
                _simple_match_score(question, text),
            )
            deduplicated.append(item)

        ranked = sorted(
            deduplicated,
            key=lambda item: (float(item.get("score") or 0.0), len(str(item.get("text") or ""))),
            reverse=True,
        )
        relevant = [item for item in ranked if float(item.get("score") or 0.0) > 0]
        candidates = relevant or ranked

        primary_item = next(
            (
                item
                for item in ranked
                if isinstance(item.get("source"), dict)
                and item["source"].get("is_primary_meeting") is True
            ),
            None,
        )
        if primary_item is not None:
            candidates = [primary_item] + [item for item in candidates if item is not primary_item]

        selected: list[dict[str, Any]] = []
        total_characters = 0
        for item in candidates:
            text = str(item.get("text") or "").strip()
            if total_characters + len(text) > _MAX_EVIDENCE_CHARACTERS:
                remaining = _MAX_EVIDENCE_CHARACTERS - total_characters
                if remaining < 300:
                    break
                item = {**item, "text": text[:remaining]}
                text = str(item["text"])
            selected.append(item)
            total_characters += len(text)
            if len(selected) >= _MAX_EVIDENCE_ITEMS:
                break
        return selected

    def _model_for_user(self, user_id: str) -> str:
        model = str(current_app.config.get("DEFAULT_AI_MODEL") or "").strip()
        try:
            settings = self.users.get_settings(user_id)
            configured = str(settings.get("aiModel") or "").strip()
            if configured:
                model = configured
        except Exception:
            # Knowledge Search can still use the deployment default if optional
            # per-user settings are temporarily unavailable.
            current_app.logger.exception(
                "Knowledge Search could not load the user's AI model preference"
            )
        if not model:
            raise ExternalServiceError("No AI model is configured for Knowledge Search.")
        return model

    @staticmethod
    def _system_message(payload: dict[str, Any]) -> str:
        instructions = [
            "You are a precise knowledge assistant for a meeting application.",
            "Answer only from the supplied sources and context.",
            "Do not invent facts. If the sources do not support the answer, say so clearly.",
            "When sources conflict, mention the conflict instead of choosing silently.",
            "Use concise, readable prose and preserve important names, dates, numbers, and conditions.",
        ]

        requested_scope = str(
            payload.get("source_scope") or payload.get("search_scope") or ""
        ).strip().lower()
        if requested_scope == "meeting_review_related":
            instructions.append(
                "Treat the selected meeting as the primary source. Use related documents "
                "or other meetings only to clarify, compare, or supplement it."
            )

        if payload.get("use_context") is not False:
            assistant_context = payload.get("assistant_context")
            if isinstance(assistant_context, dict):
                context_text = _mapping_text(assistant_context)
                if context_text:
                    instructions.append(
                        "Reusable Career Profile context (unverified; use only for personalization, "
                        "never as evidence for a candidate claim):\n" + context_text
                    )
        return "\n\n".join(instructions)

    @staticmethod
    def _user_message(
        question: str,
        selected: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> str:
        sections = []
        for index, item in enumerate(selected, start=1):
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            label = _first_text(
                source.get("filename"),
                source.get("meeting_name"),
                source.get("title"),
                f"Source {index}",
            )
            detail = _first_text(source.get("section"), source.get("collection_name"))
            header = f"Source {index}: {label}"
            if detail:
                header += f" ({detail})"
            sections.append(f"{header}\n{item['text']}")

        meeting_context = payload.get("meeting_context")
        meeting_context_text = (
            _mapping_text(meeting_context) if isinstance(meeting_context, dict) else ""
        )
        context_section = (
            "\n\nMeeting-specific context:\n" + meeting_context_text
            if meeting_context_text
            else ""
        )
        return (
            "Use the following source excerpts to answer the question. "
            "Cite sources naturally by document or meeting name when helpful."
            f"{context_section}\n\n"
            + "\n\n".join(sections)
            + f"\n\nQuestion:\n{question}"
        )


def _requested_meeting_ids(payload: dict[str, Any]) -> set[str]:
    requested = {
        str(value).strip()
        for value in _string_list(payload.get("meeting_ids"))
        if str(value).strip()
    }
    direct = str(payload.get("meeting_id") or "").strip()
    if direct:
        requested.add(direct)
    return requested


def _unique_sources(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return each underlying document or meeting once.

    Search evidence is intentionally split into sections and text chunks. Those
    excerpts may all come from the same file, so ``section`` must not be part of
    the source identity shown to the user.
    """
    sources: list[dict[str, Any]] = []
    source_indexes: dict[tuple[str, str, str], int] = {}
    for item in selected:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        normalized = dict(source)
        key = _source_identity(normalized)
        existing_index = source_indexes.get(key)
        if existing_index is not None:
            existing = sources[existing_index]
            existing["excerpt_count"] = int(existing.get("excerpt_count") or 1) + 1
            continue

        normalized["excerpt_count"] = 1
        source_indexes[key] = len(sources)
        sources.append(normalized)
    return sources


def _source_identity(source: dict[str, Any]) -> tuple[str, str, str]:
    source_type = _first_text(source.get("source_type"), source.get("type")).casefold()
    title = _first_text(
        source.get("filename"),
        source.get("display_name"),
        source.get("meeting_name"),
        source.get("title"),
    ).casefold()

    file_id = _first_text(source.get("file_id"))
    if file_id:
        return (source_type or "document", f"file:{file_id}", "")

    meeting_id = _first_text(source.get("meeting_id"))
    if meeting_id:
        # The title keeps different current-meeting materials distinct while
        # still collapsing multiple sections from the same material or meeting.
        return (source_type or "meeting", f"meeting:{meeting_id}", title)

    collection = _first_text(
        source.get("collection_id"), source.get("collection_name")
    ).casefold()
    return (source_type or "source", f"title:{title}", collection)


def _simple_match_score(question: str, text: str) -> float:
    question_tokens = _tokens(question)
    text_tokens = _tokens(text)
    if not question_tokens or not text_tokens:
        return 0.0
    overlap = question_tokens & text_tokens
    coverage = len(overlap) / len(question_tokens)
    precision = len(overlap) / min(len(text_tokens), 30)
    phrase_bonus = 0.2 if question.casefold() in text.casefold() else 0.0
    return (0.75 * coverage) + (0.25 * precision) + phrase_bonus


def _tokens(value: str) -> set[str]:
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
        "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "the",
        "this", "to", "what", "when", "where", "which", "who", "why", "with", "you",
        "your",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_.-]*", str(value or "").casefold())
        if token not in stop_words and len(token) > 1
    }


def _chunk_text(value: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    while normalized:
        if len(normalized) <= _MAX_TEXT_CHUNK_CHARACTERS:
            chunks.append(normalized)
            break
        split_at = normalized.rfind(" ", 0, _MAX_TEXT_CHUNK_CHARACTERS)
        if split_at < _MAX_TEXT_CHUNK_CHARACTERS // 2:
            split_at = _MAX_TEXT_CHUNK_CHARACTERS
        chunks.append(normalized[:split_at].strip())
        normalized = normalized[split_at:].strip()
    return chunks


def _mapping_text(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, item in value.items():
        if key in {"enabled", "storage_scope"}:
            continue
        if item is None or item == "" or item == [] or item == {}:
            continue
        label = str(key).replace("_", " ").strip().title()
        if isinstance(item, (list, tuple, set)):
            rendered = ", ".join(str(part).strip() for part in item if str(part).strip())
        elif isinstance(item, dict):
            rendered = "; ".join(
                f"{nested_key}: {nested_value}"
                for nested_key, nested_value in item.items()
                if nested_value not in (None, "", [], {})
            )
        else:
            rendered = str(item).strip()
        if rendered:
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)[:8_000]


def _searchable_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        if "L" in value and isinstance(value["L"], list):
            items: list[str] = []
            for item in value["L"]:
                items.extend(_searchable_items(item))
            return items
        scalar = _first_text(value)
        if scalar:
            return [scalar]
        mapped = _mapping_text(value)
        return [mapped] if mapped else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_searchable_items(item))
        return items
    text = str(value).strip()
    return [text] if text else []


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        if "L" in value and isinstance(value["L"], list):
            return [_first_text(item) for item in value["L"] if _first_text(item)]
        if "S" in value:
            return [str(value["S"]).strip()] if str(value["S"]).strip() else []
    if isinstance(value, (list, tuple, set)):
        return [_first_text(item) for item in value if _first_text(item)]
    text = _first_text(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            if "S" in value:
                value = value.get("S")
            elif "N" in value:
                value = value.get("N")
            else:
                continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
