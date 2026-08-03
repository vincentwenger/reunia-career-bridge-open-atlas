from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Mapping, TypeVar

from dotenv import load_dotenv
from flask import current_app, has_app_context, has_request_context, session
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError as OpenAIRateLimitError,
)
from pydantic import BaseModel, ValidationError as PydanticValidationError

from .model_config import validated_reasoning_effort
from .resume_findings import ResumeFindingsSnapshot
from .interview_preparation import (
    INTERVIEW_PREPARATION_SYSTEM,
    InterviewPreparationWorkspace,
    VerifiedEvidenceBundle,
    build_interview_preparation_prompt,
)
from .models import (
    AuditIssue,
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    NewcomerCareerProfile,
    ProposalAudit,
    TailoringProposal,
)
from .resume_import import (
    RESUME_IMPORT_SYSTEM,
    build_resume_import_prompt,
    extract_explicit_resume_summary,
    restore_professional_contact_urls,
    sanitize_imported_candidate_profile,
)
from .resume_language import (
    RESUME_TRANSLATION_SYSTEM,
    build_resume_translation_prompt,
    restore_translation_protected_fields,
    validate_translated_profile,
)
from .prompts import (
    AUDIT_FIX_SYSTEM,
    AUDIT_SYSTEM,
    JOB_ANALYSIS_SYSTEM,
    PROPOSAL_SYSTEM,
    build_audit_fix_prompt,
    build_audit_prompt,
    build_job_analysis_prompt,
    build_proposal_prompt,
    build_refinement_prompt,
)

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_OPERATION_FEATURES = {
    "create_candidate_profile_from_resume": "application_builder_resume_import",
    "translate_candidate_profile": "application_builder_career_translation",
    "analyze_job": "application_builder_job_analysis",
    "create_proposal": "application_builder_resume_tailoring",
    "refine_proposal": "application_builder_resume_tailoring",
    "audit_proposal": "application_builder_evidence_review",
    "apply_suggested_fixes": "application_builder_resume_tailoring",
    "create_interview_preparation": "application_builder_interview_preparation",
}
_OPERATION_TOKEN_CONFIG = {
    "create_candidate_profile_from_resume": ("AI_MAX_OUTPUT_TOKENS_RESUME_IMPORT", 3200),
    "translate_candidate_profile": ("AI_MAX_OUTPUT_TOKENS_RESUME_TRANSLATION", 3600),
    "analyze_job": ("AI_MAX_OUTPUT_TOKENS_RESUME_JOB_ANALYSIS", 2400),
    "create_proposal": ("AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING", 5200),
    "refine_proposal": ("AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING", 5200),
    "audit_proposal": ("AI_MAX_OUTPUT_TOKENS_RESUME_EVIDENCE_REVIEW", 3600),
    "apply_suggested_fixes": ("AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING", 5200),
    "create_interview_preparation": (
        "AI_MAX_OUTPUT_TOKENS_INTERVIEW_PREPARATION",
        4200,
    ),
}
_CACHE_VERSION = "v1"


class ResumeAIError(RuntimeError):
    """Raised when a model request cannot produce a valid structured response."""


def get_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ResumeAIError(
            "OPENAI_API_KEY is not configured. Set it as an environment variable before starting the app."
        )
    return api_key


def _configured_int(name: str, default: int, *, minimum: int = 1) -> int:
    value: Any = current_app.config.get(name, default) if has_app_context() else default
    try:
        return max(minimum, int(value or default))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _current_cost_user_id(explicit_user_id: str | None) -> str:
    normalized = str(explicit_user_id or "").strip()
    if normalized:
        return normalized
    if has_request_context():
        for key in ("user_id", "application_owner_id", "workflow_sid"):
            normalized = str(session.get(key) or "").strip()
            if normalized:
                return normalized
    return "application-builder-system"


class ResumeAI:
    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        max_attempts: int | None = None,
        user_id: str | None = None,
        request_timeout_seconds: float | None = None,
        max_output_tokens_by_operation: Mapping[str, int] | None = None,
    ) -> None:
        self.model = model.strip()
        if not self.model:
            raise ValueError("An OpenAI model name is required.")
        self.reasoning_effort = validated_reasoning_effort(self.model, reasoning_effort)
        configured_attempts = (
            max_attempts
            if max_attempts is not None
            else _configured_int("AI_APPLICATION_BUILDER_MAX_ATTEMPTS", 2)
        )
        # More than three provider attempts creates disproportionate cost and latency.
        self.max_attempts = min(3, max(1, int(configured_attempts)))
        self.user_id = _current_cost_user_id(user_id)
        try:
            request_timeout = (
                90.0
                if request_timeout_seconds is None
                else float(request_timeout_seconds)
            )
        except (TypeError, ValueError):
            request_timeout = 90.0
        self.request_timeout_seconds = min(300.0, max(1.0, request_timeout))
        self.max_output_tokens_by_operation: dict[str, int] = {}
        for operation_name, configured_value in dict(
            max_output_tokens_by_operation or {}
        ).items():
            normalized_operation = str(operation_name or "").strip()
            if not normalized_operation:
                continue
            try:
                normalized_value = max(100, int(configured_value))
            except (TypeError, ValueError):
                continue
            self.max_output_tokens_by_operation[normalized_operation] = normalized_value
        self.last_resume_import_adjustments: list[str] = []
        self.client = OpenAI(
            api_key=get_api_key(),
            timeout=self.request_timeout_seconds,
            max_retries=0,
        )

    def _parse(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        *,
        operation: str,
    ) -> T:
        max_output_tokens = self._max_output_tokens(operation)
        feature = _OPERATION_FEATURES.get(operation, "application_builder_ai")
        prompt_characters = len(system_prompt) + len(user_prompt)
        cache_key = self._cache_key(
            system_prompt,
            user_prompt,
            schema,
            operation=operation,
            max_output_tokens=max_output_tokens,
        )
        cached = self._cache_get(cache_key, schema)
        if cached is not None:
            logger.info(
                "AI cache hit: operation=%s model=%s effort=%s",
                operation,
                self.model,
                self.reasoning_effort or "none",
            )
            return cached

        last_error: Exception | None = None
        started_at = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            reservation = None
            try:
                cost_control = self._cost_control_service()
                if cost_control is not None:
                    reservation = cost_control.reserve_text_request(
                        self.user_id,
                        feature=feature,
                        model=self.model,
                        prompt_characters=prompt_characters,
                        max_output_tokens=max_output_tokens,
                    )

                request: dict[str, object] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": schema,
                    "max_completion_tokens": max_output_tokens,
                }
                if self.reasoning_effort is not None:
                    request["reasoning_effort"] = self.reasoning_effort

                completion = self.client.chat.completions.parse(**request)
                if reservation is not None and cost_control is not None:
                    actual_cost = cost_control.usage_cost_usd(
                        self.model, getattr(completion, "usage", None)
                    )
                    # Unknown custom models keep the full conservative reserve;
                    # configured models settle to their measured token cost.
                    reservation.settle(actual_cost)

                message = completion.choices[0].message
                refusal = getattr(message, "refusal", None)
                if refusal:
                    raise ResumeAIError(f"The model declined the request: {refusal}")
                parsed = message.parsed
                if parsed is None:
                    raise ResumeAIError("The model returned no structured result.")

                self._record_usage(completion, feature, started_at)
                self._cache_set(cache_key, parsed)
                logger.info(
                    "AI timing: operation=%s model=%s effort=%s attempt=%s elapsed=%.2fs",
                    operation,
                    self.model,
                    self.reasoning_effort or "none",
                    attempt,
                    time.perf_counter() - started_at,
                )
                return parsed
            except ResumeAIError:
                if reservation is not None:
                    reservation.release()
                raise
            except (
                OpenAIRateLimitError,
                APITimeoutError,
                APIConnectionError,
                APIStatusError,
            ) as exc:
                if reservation is not None:
                    # A timeout, connection interruption, or provider 5xx can occur
                    # after processing began. Retain the conservative reservation in
                    # those cases; a clean 429 is known not to have consumed tokens.
                    if isinstance(exc, OpenAIRateLimitError):
                        reservation.release()
                    else:
                        reservation.settle(None)
                last_error = exc
                limited_message = self._openai_limit_message(exc)
                if limited_message:
                    raise ResumeAIError(limited_message) from exc
                if not self._is_retryable_provider_error(exc) or attempt >= self.max_attempts:
                    break
                time.sleep(2 ** (attempt - 1))
            except Exception as exc:
                # Structured-output parsing raises an SDK exception when the model
                # consumes the whole completion budget before finishing the JSON.
                # The provider has already processed/billed this request, so retain
                # the conservative reservation rather than treating it as unused.
                if self._is_length_limit_error(exc):
                    if reservation is not None:
                        reservation.settle(None)
                    raise ResumeAIError(
                        "The AI response reached its output-token limit before "
                        "producing complete structured data. This job can be "
                        "retried later."
                    ) from exc

                # Includes SDK/schema incompatibilities and application budget limits.
                if reservation is not None:
                    reservation.release()
                budget_message = self._application_limit_message(exc)
                if budget_message:
                    raise ResumeAIError(budget_message) from exc
                last_error = exc
                break

        detail = str(last_error) if last_error else "Unknown API error"
        logger.warning(
            "AI timing: operation=%s model=%s effort=%s failed elapsed=%.2fs detail=%s",
            operation,
            self.model,
            self.reasoning_effort or "none",
            time.perf_counter() - started_at,
            detail,
        )
        raise ResumeAIError(
            f"OpenAI request using {self.model} failed after {self.max_attempts} attempt(s): {detail}"
        )

    def _max_output_tokens(self, operation: str) -> int:
        explicit_override = self.max_output_tokens_by_operation.get(operation)
        if explicit_override is not None:
            return explicit_override
        config_name, default = _OPERATION_TOKEN_CONFIG.get(
            operation,
            ("AI_MAX_OUTPUT_TOKENS_APPLICATION_BUILDER", 4000),
        )
        return _configured_int(config_name, default, minimum=100)

    @staticmethod
    def _is_length_limit_error(exc: Exception) -> bool:
        error_name = type(exc).__name__.casefold()
        detail = str(exc).casefold()
        return (
            "lengthfinishreasonerror" in error_name
            or "length limit was reached" in detail
            or "finish_reason='length'" in detail
            or 'finish_reason="length"' in detail
            or "finish reason was length" in detail
        )

    def _cache_key(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        *,
        operation: str,
        max_output_tokens: int,
    ) -> str:
        payload = {
            "version": _CACHE_VERSION,
            "user": self.user_id,
            "operation": operation,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": max_output_tokens,
            "schema": f"{schema.__module__}.{schema.__qualname__}",
            "system": system_prompt,
            "user_prompt": user_prompt,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"application-builder:{_CACHE_VERSION}:{digest}"

    @staticmethod
    def _cache() -> Any | None:
        if not has_app_context():
            return None
        return current_app.extensions.get("ai_response_cache")

    def _cache_get(self, cache_key: str, schema: type[T]) -> T | None:
        cache = self._cache()
        if cache is None:
            return None
        try:
            cached = cache.get(cache_key)
        except Exception:
            if has_app_context():
                current_app.logger.exception("Could not read the Application Builder AI cache")
            return None
        if not isinstance(cached, dict):
            return None
        try:
            return schema.model_validate(cached.get("value"))
        except (PydanticValidationError, TypeError, ValueError):
            return None

    def _cache_set(self, cache_key: str, parsed: BaseModel) -> None:
        cache = self._cache()
        if cache is None:
            return
        ttl = _configured_int(
            "AI_APPLICATION_BUILDER_CACHE_SECONDS",
            _configured_int("AI_RESPONSE_CACHE_SECONDS", 3600),
            minimum=60,
        )
        try:
            cache.set(
                cache_key,
                {"value": parsed.model_dump(mode="json")},
                ttl,
            )
        except Exception:
            if has_app_context():
                current_app.logger.exception("Could not write the Application Builder AI cache")

    @staticmethod
    def _cost_control_service() -> Any | None:
        if not has_app_context():
            return None
        # Imported lazily so the resume-tailoring package remains usable in
        # standalone tooling while the combined product reuses Réunia services.
        from meeting_assistant.services.ai_cost_control_service import (
            AICostControlService,
        )

        return AICostControlService()

    @staticmethod
    def _application_limit_message(error: Exception) -> str:
        if not has_app_context():
            return ""
        from meeting_assistant.utils.exceptions import RateLimitError

        return str(error) if isinstance(error, RateLimitError) else ""

    @staticmethod
    def _openai_limit_message(error: Exception) -> str:
        if not has_app_context():
            return ""
        from meeting_assistant.services.ai_cost_control_service import (
            raise_if_openai_limited,
        )
        from meeting_assistant.utils.exceptions import RateLimitError

        try:
            raise_if_openai_limited(error)
        except RateLimitError as exc:
            return str(exc)
        return ""

    @staticmethod
    def _is_retryable_provider_error(error: Exception) -> bool:
        if isinstance(error, OpenAIRateLimitError):
            return False
        if isinstance(error, (APITimeoutError, APIConnectionError)):
            return True
        status_code = getattr(error, "status_code", None)
        return (
            isinstance(error, APIStatusError)
            and isinstance(status_code, int)
            and status_code >= 500
        )

    def _record_usage(self, completion: Any, feature: str, started_at: float) -> None:
        if not has_app_context():
            return
        try:
            from meeting_assistant.services.admin_analytics_service import (
                UsageMetricsService,
            )

            UsageMetricsService().record_ai_response(
                self.user_id,
                completion,
                feature=feature,
                model=self.model,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
        except Exception:
            current_app.logger.exception(
                "Could not record Application Builder AI usage for %s", feature
            )

    def create_interview_preparation(
        self,
        *,
        company: str,
        role: str,
        job_description: str,
        evidence: VerifiedEvidenceBundle,
        resume_findings: ResumeFindingsSnapshot,
        interview_audience: str = "",
        career_profile_context: dict[str, str] | None = None,
    ) -> InterviewPreparationWorkspace:
        workspace = self._parse(
            INTERVIEW_PREPARATION_SYSTEM,
            build_interview_preparation_prompt(
                company=company,
                role=role,
                interview_audience=interview_audience,
                job_description=job_description,
                evidence=evidence,
                resume_findings=resume_findings,
                career_profile_context=career_profile_context,
            ),
            InterviewPreparationWorkspace,
            operation="create_interview_preparation",
        )
        from .interview_preparation import restrict_workspace_to_evidence

        return restrict_workspace_to_evidence(
            workspace,
            evidence.ids,
            submitted_resume_ids=evidence.submitted_resume_ids,
            evidence_by_id={item.id: item.text for item in evidence.items},
        )

    def create_candidate_profile_from_resume(
        self,
        *,
        resume_text: str,
        filename: str,
    ) -> CandidateProfile:
        """Extract a fresh profile using only the newly uploaded resume.

        A replacement import must never be blocked because the extractor used one
        untraceable paraphrase. Deterministic sanitization omits only unsupported
        fields while preserving the rest of the newly uploaded resume.
        """

        self.last_resume_import_adjustments = []
        explicit_summary = extract_explicit_resume_summary(resume_text)
        profile = self._parse(
            RESUME_IMPORT_SYSTEM,
            build_resume_import_prompt(resume_text, filename),
            CandidateProfile,
            operation="create_candidate_profile_from_resume",
        )
        profile = restore_professional_contact_urls(profile, resume_text)
        from .grounding import validate_candidate_claim

        findings = validate_candidate_claim(
            profile.all_source_text(),
            [resume_text],
            require_overlap=False,
        )
        if findings:
            profile, removed = sanitize_imported_candidate_profile(
                profile, resume_text
            )
            self.last_resume_import_adjustments = removed
            final_findings = validate_candidate_claim(
                profile.all_source_text(),
                [resume_text],
                require_overlap=False,
            )
            if final_findings:
                # The sanitizer is intentionally fail-closed: no unsupported
                # generated source text is retained in the reusable baseline.
                logger.warning(
                    "Resume replacement retained residual grounding findings after cleanup: %s",
                    "; ".join(item.message for item in final_findings[:4]),
                )
                profile.current_summary = ""
                profile.skills.hard_skills = []
                profile.skills.soft_skills = []
                profile.skills.tools_software = []
                profile.skills.industry_knowledge = []
                profile.skills.languages = []
                profile.education = []
                profile.experiences = []
                profile.supplemental_evidence = []
                self.last_resume_import_adjustments = [
                    "structured resume details that required manual review"
                ]

        # The user's authored summary is source evidence, not generated copy.
        # Apply it after grounding cleanup so the exact uploaded wording wins
        # over an AI paraphrase and is not normalized into different sentences.
        if explicit_summary:
            profile.current_summary = explicit_summary
        return profile

    def translate_candidate_profile(
        self,
        profile: CandidateProfile,
        *,
        target_language: str,
        target_country: str = "",
    ) -> CandidateProfile:
        translated = self._parse(
            RESUME_TRANSLATION_SYSTEM,
            build_resume_translation_prompt(
                profile,
                target_language=target_language,
                target_country=target_country,
            ),
            CandidateProfile,
            operation="translate_candidate_profile",
        )
        translated = restore_translation_protected_fields(profile, translated)
        issues = validate_translated_profile(
            profile, translated, target_language
        )
        if issues:
            raise ResumeAIError(
                "The resume translation did not preserve the Verified Resume Evidence: "
                + "; ".join(issues[:4])
            )
        return translated

    def analyze_job(self, job_description: str, stated_title: str = "") -> JobAnalysis:
        return self._parse(
            JOB_ANALYSIS_SYSTEM,
            build_job_analysis_prompt(job_description, stated_title),
            JobAnalysis,
            operation="analyze_job",
        )

    def _retry_incomplete_bullet_selection(
        self,
        *,
        profile: CandidateProfile,
        prompt: str,
        proposal: TailoringProposal,
        operation: str,
    ) -> TailoringProposal:
        """Retry once when the model omits source-bullet evidence-mapping records."""

        from .proposal_integrity import missing_source_bullet_ids

        missing_ids = missing_source_bullet_ids(profile, proposal)
        if not missing_ids:
            return proposal

        retry_prompt = (
            prompt
            + "\n\nSTRUCTURAL RETRY REQUIRED:\n"
            + (
                "The previous structured response omitted a bullet_proposals evidence-mapping "
                "record for: "
            )
            + ", ".join(missing_ids)
            + (
                ". Return a complete TailoringProposal with exactly one record for every "
                "source bullet ID. Every record must set include=false and contain source-backed "
                "proposed_text, matched_requirement_ids, and an evidence-basis note. "
                "Do not omit any source bullet."
            )
        )
        try:
            retried = self._parse(
                PROPOSAL_SYSTEM,
                retry_prompt,
                TailoringProposal,
                operation=operation,
            )
        except ResumeAIError as exc:
            logger.warning(
                "Bullet-mapping retry failed; deterministic selection will use "
                "the original proposal: operation=%s detail=%s",
                operation,
                exc,
            )
            return proposal
        retried_missing_ids = missing_source_bullet_ids(profile, retried)
        if len(retried_missing_ids) < len(missing_ids):
            logger.info(
                "Recovered incomplete bullet mapping: operation=%s missing_before=%s missing_after=%s",
                operation,
                len(missing_ids),
                len(retried_missing_ids),
            )
            return retried
        logger.warning(
            "Bullet-mapping retry remained incomplete: operation=%s missing_ids=%s",
            operation,
            ",".join(missing_ids),
        )
        return proposal

    def create_proposal(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        career_background: NewcomerCareerProfile | None = None,
    ) -> TailoringProposal:
        prompt = build_proposal_prompt(profile, analysis, career_background)
        proposal = self._parse(
            PROPOSAL_SYSTEM,
            prompt,
            TailoringProposal,
            operation="create_proposal",
        )
        proposal = self._retry_incomplete_bullet_selection(
            profile=profile,
            prompt=prompt,
            proposal=proposal,
            operation="create_proposal",
        )
        from .deterministic_fixes import repair_unsupported_candidate_claims

        return repair_unsupported_candidate_claims(profile, analysis, proposal)

    def refine_proposal(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        provisional: TailoringProposal,
        answers: list[CandidateAnswer],
        career_background: NewcomerCareerProfile | None = None,
    ) -> TailoringProposal:
        prompt = build_refinement_prompt(
            profile, analysis, provisional, answers, career_background
        )
        proposal = self._parse(
            PROPOSAL_SYSTEM,
            prompt,
            TailoringProposal,
            operation="refine_proposal",
        )
        proposal = self._retry_incomplete_bullet_selection(
            profile=profile,
            prompt=prompt,
            proposal=proposal,
            operation="refine_proposal",
        )
        from .deterministic_fixes import repair_unsupported_candidate_claims

        return repair_unsupported_candidate_claims(profile, analysis, proposal)

    def audit_proposal(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        proposal: TailoringProposal,
        career_background: NewcomerCareerProfile | None = None,
    ) -> ProposalAudit:
        return self._parse(
            AUDIT_SYSTEM,
            build_audit_prompt(profile, analysis, proposal, career_background),
            ProposalAudit,
            operation="audit_proposal",
        )

    def apply_suggested_fixes(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        proposal: TailoringProposal,
        issues: list[AuditIssue],
        career_background: NewcomerCareerProfile | None = None,
    ) -> TailoringProposal:
        actionable = [issue for issue in issues if issue.suggested_fix.strip()]
        if not actionable:
            raise ResumeAIError("The selected recommendation does not contain a suggested fix.")
        corrected = self._parse(
            AUDIT_FIX_SYSTEM,
            build_audit_fix_prompt(
                profile, analysis, proposal, actionable, career_background
            ),
            TailoringProposal,
            operation="apply_suggested_fixes",
        )
        from .deterministic_fixes import repair_unsupported_candidate_claims

        return repair_unsupported_candidate_claims(profile, analysis, corrected)
