"""Declarative expected-versus-actual test cases used by run_tests.py."""

from dataclasses import dataclass
from typing import Any, Callable

from flask import url_for

from meeting_assistant.services.scoring_service import calculate_overall_performance_score
from meeting_assistant.utils.json_parsing import (
    normalize_transcript_item,
    parse_content_grades,
    parse_form_metrics,
    parse_meeting_insights,
)


@dataclass(frozen=True)
class ExpectedActualCase:
    name: str
    expected: Any
    get_actual: Callable[[Any], Any]
    description: str = ""


def _registered_routes(app):
    return sorted(rule.rule for rule in app.url_map.iter_rules() if not rule.build_only)


def build_cases(app):
    """Return independent cases that compare an expected value with an actual value."""
    return [
        ExpectedActualCase(
            name="Public marketing route is registered",
            expected=True,
            get_actual=lambda current_app: "/" in _registered_routes(current_app),
        ),
        ExpectedActualCase(
            name="Analytics route is registered",
            expected=True,
            get_actual=lambda current_app: "/analytics.html" in _registered_routes(current_app),
        ),
        ExpectedActualCase(
            name="User Guide route is public",
            expected=200,
            get_actual=lambda current_app: current_app.test_client().get("/user-guide.html").status_code,
        ),
        ExpectedActualCase(
            name="Unknown route returns 404",
            expected=404,
            get_actual=lambda current_app: current_app.test_client().get("/does-not-exist").status_code,
        ),
        ExpectedActualCase(
            name="Transcript API requires authentication",
            expected={"status": 401, "json": {"error": "Authentication required."}},
            get_actual=lambda current_app: _response_summary(
                current_app.test_client().get("/api/transcripts")
            ),
        ),
        ExpectedActualCase(
            name="Legacy app endpoint builds correctly",
            expected="/app",
            get_actual=lambda current_app: _build_url(current_app, "view_index"),
        ),
        ExpectedActualCase(
            name="Meeting insight parser extracts action items",
            expected={"meeting_name": "Planning", "action_items": ["Ship"]},
            get_actual=lambda _app: _insight_summary(),
        ),
        ExpectedActualCase(
            name="Content grade parser returns native list",
            expected=[{"question": "Q", "answer": "A", "relevance_analysis": "Direct", "grade": "A"}],
            get_actual=lambda _app: parse_content_grades(
                '{"content_grades":[{"question":"Q","answer":"A","relevance_analysis":"Direct","grade":"A"}]}'
            ),
        ),
        ExpectedActualCase(
            name="Null form metrics produce empty pace",
            expected=None,
            get_actual=lambda _app: parse_form_metrics("null")["pace_wpm"],
        ),
        ExpectedActualCase(
            name="Legacy DynamoDB values are normalized",
            expected={"content_grades": [{"grade": "A"}], "key_wins": ["Good outcome"]},
            get_actual=lambda _app: normalize_transcript_item(
                {
                    "content_grades": {"L": [{"M": {"grade": {"S": "A"}}}]},
                    "key_wins": {"L": [{"S": "Good outcome"}]},
                }
            ),
        ),
        ExpectedActualCase(
            name="Overall performance score is calculated",
            expected={
                "content_average_score": 89.0,
                "form_average_score": 89.0,
                "final_grade": 89.0,
            },
            get_actual=lambda _app: calculate_overall_performance_score(
                [{"grade": "A"}, {"grade": "B"}],
                {"pace_grade": "A", "filler_words_grade": "B"},
            ),
        ),
        ExpectedActualCase(
            name="Empty grades produce no score",
            expected={
                "content_average_score": None,
                "form_average_score": None,
                "final_grade": None,
            },
            get_actual=lambda _app: calculate_overall_performance_score([], {}),
        ),
        ExpectedActualCase(
            name="Invalid support email is rejected",
            expected=400,
            get_actual=lambda current_app: current_app.test_client().post(
                "/api/support",
                data={
                    "name": "Test User",
                    "email": "invalid-email",
                    "topic": "other",
                    "subject": "Question",
                    "message": "Please help with this question.",
                },
                headers={"Accept": "application/json"},
            ).status_code,
        ),
    ]


def _response_summary(response):
    return {"status": response.status_code, "json": response.get_json(silent=True)}


def _build_url(app, endpoint):
    with app.test_request_context():
        return url_for(endpoint)


def _insight_summary():
    result = parse_meeting_insights(
        '{"meeting_name":"Planning","summary":"Summary","action_items":["Ship"],"open_questions":[]}'
    )
    return {
        "meeting_name": result["meeting_name"],
        "action_items": result["action_items"],
    }
