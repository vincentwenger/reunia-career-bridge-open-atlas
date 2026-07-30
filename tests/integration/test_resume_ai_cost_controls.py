from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

_REQUIRED_MODULES = ("flask", "redis", "openai")
_MISSING_MODULES = tuple(
    name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None
)
_RUNTIME_AVAILABLE = not _MISSING_MODULES
_SKIP_REASON = "Missing runtime dependencies: " + ", ".join(_MISSING_MODULES)

ROOT = Path(__file__).resolve().parents[2]
for source_root in (ROOT / "products" / "reunia", ROOT / "products" / "resume_taylor"):
    source = str(source_root)
    if source not in sys.path:
        sys.path.insert(0, source)

os.environ.setdefault("OPENAI_API_KEY", "test-key")

if _RUNTIME_AVAILABLE:
    from flask import Flask, session
    from meeting_assistant.services.security_service import MemoryTTLCache  # noqa: E402
    from resume_tailor.ai import ResumeAI  # noqa: E402
else:
    Flask = None
    session = None
    MemoryTTLCache = None
    ResumeAI = None


class ParsedResult(BaseModel):
    value: str


class FakeReservation:
    def __init__(self) -> None:
        self.reserved_cost_usd = 0.05
        self.settled_cost = None
        self.released = False

    def settle(self, actual_cost_usd):
        self.settled_cost = actual_cost_usd

    def release(self):
        self.released = True


class FakeCostControl:
    def __init__(self) -> None:
        self.reservations: list[tuple[dict, FakeReservation]] = []

    def reserve_text_request(self, user_id, **kwargs):
        reservation = FakeReservation()
        self.reservations.append(({"user_id": user_id, **kwargs}, reservation))
        return reservation

    @staticmethod
    def usage_cost_usd(model, usage):
        if model != "gpt-4o-mini" or usage.prompt_tokens != 20:
            raise AssertionError("Unexpected model or usage report")
        return 0.012


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        parsed=ParsedResult(value="bounded and cached"),
                        refusal=None,
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )


def _test_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config.update(
        AI_MAX_OUTPUT_TOKENS_RESUME_JOB_ANALYSIS=777,
        AI_APPLICATION_BUILDER_MAX_ATTEMPTS=2,
        AI_APPLICATION_BUILDER_CACHE_SECONDS=600,
        AI_RESPONSE_CACHE_SECONDS=600,
    )
    app.extensions["ai_response_cache"] = MemoryTTLCache()
    return app


@unittest.skipUnless(_RUNTIME_AVAILABLE, _SKIP_REASON)
class ResumeAICostControlTests(unittest.TestCase):
    def test_budget_output_settlement_and_cache(self):
        app = _test_app()
        cost_control = FakeCostControl()
        completions = FakeCompletions()

        with app.test_request_context("/applications/workflow/start"):
            session["user_id"] = "candidate@example.com"
            ai = ResumeAI("gpt-4o-mini")
            ai.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

            with (
                patch.object(
                    ResumeAI,
                    "_cost_control_service",
                    staticmethod(lambda: cost_control),
                ),
                patch.object(ResumeAI, "_record_usage", autospec=True),
            ):
                first = ai._parse(
                    "system instructions",
                    "job description",
                    ParsedResult,
                    operation="analyze_job",
                )
                second = ai._parse(
                    "system instructions",
                    "job description",
                    ParsedResult,
                    operation="analyze_job",
                )

        self.assertEqual(first, ParsedResult(value="bounded and cached"))
        self.assertEqual(second, first)
        self.assertEqual(len(completions.calls), 1)
        self.assertEqual(completions.calls[0]["max_completion_tokens"], 777)
        self.assertEqual(len(cost_control.reservations), 1)
        reservation_request, reservation = cost_control.reservations[0]
        self.assertEqual(reservation_request["user_id"], "candidate@example.com")
        self.assertEqual(
            reservation_request["feature"],
            "application_builder_job_analysis",
        )
        self.assertEqual(reservation_request["max_output_tokens"], 777)
        self.assertEqual(reservation.settled_cost, 0.012)
        self.assertFalse(reservation.released)

    def test_attempt_defaults_and_cap(self):
        app = _test_app()
        with app.test_request_context("/"):
            self.assertEqual(ResumeAI("gpt-4o-mini").max_attempts, 2)
            self.assertEqual(ResumeAI("gpt-4o-mini", max_attempts=99).max_attempts, 3)
            self.assertEqual(ResumeAI("gpt-4o-mini", max_attempts=0).max_attempts, 1)


if __name__ == "__main__":
    unittest.main()
