from __future__ import annotations

import os
import logging
import time
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel
from dotenv import load_dotenv

from .model_config import validated_reasoning_effort
from .models import (
    AuditIssue,
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    NewcomerCareerProfile,
    ProposalAudit,
    TailoringProposal,
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


class ResumeAIError(RuntimeError):
    """Raised when a model request cannot produce a valid structured response."""


def get_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ResumeAIError(
            "OPENAI_API_KEY is not configured. Set it as an environment variable before starting the app."
        )
    return api_key


class ResumeAI:
    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.model = model.strip()
        if not self.model:
            raise ValueError("An OpenAI model name is required.")
        self.reasoning_effort = validated_reasoning_effort(self.model, reasoning_effort)
        self.max_attempts = max(1, max_attempts)
        self.client = OpenAI(api_key=get_api_key(), timeout=90.0, max_retries=0)

    def _parse(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        *,
        operation: str,
    ) -> T:
        last_error: Exception | None = None
        started_at = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                request: dict[str, object] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": schema,
                }
                if self.reasoning_effort is not None:
                    request["reasoning_effort"] = self.reasoning_effort
                completion = self.client.chat.completions.parse(**request)
                message = completion.choices[0].message
                refusal = getattr(message, "refusal", None)
                if refusal:
                    raise ResumeAIError(f"The model declined the request: {refusal}")
                parsed = message.parsed
                if parsed is None:
                    raise ResumeAIError("The model returned no structured result.")
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
                raise
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                break
            except Exception as exc:  # Includes SDK/schema incompatibilities.
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

    def analyze_job(self, job_description: str, stated_title: str = "") -> JobAnalysis:
        return self._parse(
            JOB_ANALYSIS_SYSTEM,
            build_job_analysis_prompt(job_description, stated_title),
            JobAnalysis,
            operation="analyze_job",
        )

    def create_proposal(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        career_background: NewcomerCareerProfile | None = None,
    ) -> TailoringProposal:
        return self._parse(
            PROPOSAL_SYSTEM,
            build_proposal_prompt(profile, analysis, career_background),
            TailoringProposal,
            operation="create_proposal",
        )

    def refine_proposal(
        self,
        profile: CandidateProfile,
        analysis: JobAnalysis,
        provisional: TailoringProposal,
        answers: list[CandidateAnswer],
        career_background: NewcomerCareerProfile | None = None,
    ) -> TailoringProposal:
        return self._parse(
            PROPOSAL_SYSTEM,
            build_refinement_prompt(
                profile, analysis, provisional, answers, career_background
            ),
            TailoringProposal,
            operation="refine_proposal",
        )

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
        return self._parse(
            AUDIT_FIX_SYSTEM,
            build_audit_fix_prompt(
                profile, analysis, proposal, actionable, career_background
            ),
            TailoringProposal,
            operation="apply_suggested_fixes",
        )
