"""Dependency-free validation for Application Builder AI cost protections."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AI_MODULE = ROOT / "products/resume_taylor/resume_tailor/ai.py"
CONFIG = ROOT / "products/reunia/meeting_assistant/config.py"


class ValidationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def main() -> int:
    ai_text = AI_MODULE.read_text(encoding="utf-8")
    config_text = CONFIG.read_text(encoding="utf-8")
    ast.parse(ai_text)
    ast.parse(config_text)

    checks = {
        "shared budget reservation": "reserve_text_request(" in ai_text,
        "actual usage settlement": "usage_cost_usd(" in ai_text and "reservation.settle(" in ai_text,
        "bounded structured output": '"max_completion_tokens": max_output_tokens' in ai_text,
        "shared response cache": 'current_app.extensions.get("ai_response_cache")' in ai_text,
        "user-scoped cache key": '"user": self.user_id' in ai_text,
        "retry ceiling": 'AI_APPLICATION_BUILDER_MAX_ATTEMPTS' in ai_text and "min(3" in ai_text,
        "resume import token limit": "AI_MAX_OUTPUT_TOKENS_RESUME_IMPORT" in config_text,
        "job analysis token limit": "AI_MAX_OUTPUT_TOKENS_RESUME_JOB_ANALYSIS" in config_text,
        "tailoring token limit": "AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING" in config_text,
        "evidence-review token limit": "AI_MAX_OUTPUT_TOKENS_RESUME_EVIDENCE_REVIEW" in config_text,
        "interview-preparation token limit": "AI_MAX_OUTPUT_TOKENS_INTERVIEW_PREPARATION" in config_text,
        "cache TTL configuration": "AI_APPLICATION_BUILDER_CACHE_SECONDS" in config_text,
        "selected-model pricing": all(
            model in config_text
            for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
        ),
    }
    for label, passed in checks.items():
        require(passed, f"Missing protection: {label}")
        print(f"PASS: {label}")
    print(f"All {len(checks)} Application Builder AI cost-control checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
