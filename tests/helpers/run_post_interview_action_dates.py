"""Dependency-light regression runner for interview schedule action dates."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers.run_scorecard_action_integration import (
    _EmptyWorkflowStore,
    _TranscriptService,
    _load_runtime_modules,
)


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


def main() -> int:
    ActionService, InMemoryActionRepository, make_application_store = (
        _load_runtime_modules()
    )

    owner_id = "post-interview-date-user"
    application_store = make_application_store()
    repository = InMemoryActionRepository()

    yesterday = application_store.create(
        owner_id,
        company="Northwest Systems",
        role="Senior Data Engineer",
        status="interviewing",
        upcoming_event_date=_iso(-1),
        upcoming_event_type="interview",
    )
    two_days_ago = application_store.create(
        owner_id,
        company="Cascade Bank",
        role="Platform Engineer",
        status="interviewing",
        upcoming_event_date=_iso(-2),
        upcoming_event_type="interview",
    )
    today = application_store.create(
        owner_id,
        company="Rose City Analytics",
        role="Data Engineer",
        status="interviewing",
        upcoming_event_date=_iso(0),
        upcoming_event_type="interview",
    )
    future = application_store.create(
        owner_id,
        company="Columbia Software",
        role="Software Engineer",
        status="interviewing",
        upcoming_event_date=_iso(3),
        upcoming_event_type="interview",
    )
    old = application_store.create(
        owner_id,
        company="Old Interview Co",
        role="Engineer",
        status="interviewing",
        upcoming_event_date=_iso(-15),
        upcoming_event_type="interview",
    )

    service = ActionService(
        repository=repository,
        transcript_service=_TranscriptService([]),
        application_store=application_store,
        workflow_store=_EmptyWorkflowStore(),
    )
    actions = service.list_for_user(owner_id)

    thank_you_actions = {
        action["application_id"]: action
        for action in actions
        if str(action.get("source_reference") or "").startswith(
            "interview-thank-you:"
        )
    }
    upcoming_actions = {
        action["application_id"]: action
        for action in actions
        if action.get("source") == "upcoming_interview"
    }

    assert set(thank_you_actions) == {yesterday.id, two_days_ago.id}, thank_you_actions
    assert thank_you_actions[yesterday.id]["due_date"] == _iso(0)
    assert thank_you_actions[two_days_ago.id]["due_date"] == _iso(-1)
    assert thank_you_actions[yesterday.id]["due_date"] != _iso(-1)
    assert "the day after the interview" in thank_you_actions[yesterday.id][
        "source_detail"
    ]

    assert set(upcoming_actions) == {today.id, future.id}, upcoming_actions
    assert upcoming_actions[today.id]["due_date"] == _iso(0)
    assert upcoming_actions[future.id]["due_date"] == _iso(3)
    assert old.id not in thank_you_actions
    assert old.id not in upcoming_actions

    completed = service.update(
        owner_id,
        thank_you_actions[yesterday.id]["action_id"],
        {
            "application_id": yesterday.id,
            "status": "done",
        },
    )
    assert completed["status"] == "done"
    assert completed["completed_at"]

    refreshed = service.list_for_user(owner_id)
    completed_matches = [
        action
        for action in refreshed
        if action["action_id"] == thank_you_actions[yesterday.id]["action_id"]
    ]
    assert len(completed_matches) == 1, completed_matches
    assert completed_matches[0]["status"] == "done"
    assert completed_matches[0]["due_date"] == _iso(0)

    print(
        json.dumps(
            {
                "status": "passed",
                "yesterday_interview_due_date": thank_you_actions[yesterday.id][
                    "due_date"
                ],
                "older_interview_due_date": thank_you_actions[two_days_ago.id][
                    "due_date"
                ],
                "upcoming_action_count": len(upcoming_actions),
                "completed_action_preserved": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
