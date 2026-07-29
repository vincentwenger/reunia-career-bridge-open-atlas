from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from flask import current_app

from meeting_assistant.services.action_service import ActionService
from meeting_assistant.services.transcript_service import TranscriptService


def _number(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _date_key(value: Any) -> str:
    return str(value or "")


def _application_id_from_review(review: dict[str, Any]) -> str:
    return str(
        review.get("career_application_id")
        or review.get("application_id")
        or ""
    ).strip()


def _review_score(review: dict[str, Any]) -> float | None:
    scorecard = review.get("interview_scorecard")
    if isinstance(scorecard, dict):
        score = _number(scorecard.get("overall_score"))
        if score is not None:
            return score
    return _number(review.get("overall_score") or review.get("final_grade"))


def _weak_answers_with_guidance(review: dict[str, Any]) -> int:
    count = 0
    for item in review.get("interview_answer_reviews") or []:
        if not isinstance(item, dict):
            continue
        score = _number(item.get("score"))
        original = " ".join(str(item.get("answer") or "").split()).casefold()
        improved = " ".join(
            str(item.get("sample_improved_answer") or "").split()
        ).casefold()
        if score is not None and score < 70 and improved and improved != original:
            count += 1
    return count


def _score_progress(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [
        {
            "score": _review_score(review),
            "timestamp": str(review.get("timestamp") or ""),
            "meeting_name": str(review.get("meeting_name") or "Mock interview"),
        }
        for review in sorted(reviews, key=lambda item: _date_key(item.get("timestamp")))
    ]
    scored = [item for item in scored if item["score"] is not None]
    first = scored[0]["score"] if scored else None
    latest = scored[-1]["score"] if scored else None
    improvement = (
        round(float(latest) - float(first), 1)
        if len(scored) >= 2 and first is not None and latest is not None
        else None
    )
    return {
        "sessions": len(reviews),
        "scored_sessions": len(scored),
        "first_score": first,
        "latest_score": latest,
        "improvement": improvement,
        "trend": scored,
    }


class CareerImpactService:
    """Assemble defensible social-impact outcomes from Career Bridge records."""

    def build(self, user_id: str) -> dict[str, Any]:
        application_store = current_app.extensions.get(
            "career_bridge_application_store"
        )
        workflow_store = current_app.extensions.get("career_bridge_workflow_store")
        if application_store is None:
            return self._empty_payload(
                "Application Builder storage is not available in this deployment."
            )

        warnings: list[str] = []
        applications = application_store.list_for_owner(user_id)
        persisted = {
            str(item.get("application_id") or ""): item
            for item in application_store.list_impact_snapshots(user_id)
        }

        # Live workflow measurements override older persisted values while the
        # candidate continues working, but are not invented for untouched apps.
        try:
            from resume_tailor.impact_tracking import build_workflow_impact_snapshot
        except ImportError:
            build_workflow_impact_snapshot = None
            warnings.append("Live resume-workflow impact measurements are unavailable.")

        impact_by_application: dict[str, dict[str, Any]] = dict(persisted)
        if build_workflow_impact_snapshot is not None and workflow_store is not None:
            peek = getattr(workflow_store, "peek", None)
            if callable(peek):
                for application in applications:
                    workflow_key = f"{user_id}:application:{application.id}"
                    state = peek(workflow_key)
                    if state is None:
                        continue
                    live = build_workflow_impact_snapshot(state)
                    if not live.get("measured"):
                        continue
                    impact_by_application[application.id] = {
                        "application_id": application.id,
                        "owner_id": user_id,
                        **live,
                        "details": live,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "live": True,
                    }

        try:
            reviews = TranscriptService().list_for_user(user_id)
        except Exception:
            current_app.logger.exception("Could not load interview reviews for impact")
            reviews = []
            warnings.append("Interview-practice outcomes could not be loaded.")
        reviews = [
            item
            for item in reviews
            if isinstance(item, dict)
            and (
                item.get("scorecard_type") == "interview"
                or item.get("interview_scorecard")
                or str(item.get("meeting_id") or "").startswith("mock-interview-")
            )
        ]

        try:
            actions = ActionService().list_for_user(user_id)
        except Exception:
            current_app.logger.exception("Could not load Career Action Plan for impact")
            actions = []
            warnings.append("Career Action Plan outcomes could not be loaded.")

        reviews_by_application: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unlinked_reviews: list[dict[str, Any]] = []
        for review in reviews:
            application_id = _application_id_from_review(review)
            if application_id:
                reviews_by_application[application_id].append(review)
            else:
                unlinked_reviews.append(review)

        actions_by_application: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for action in actions:
            application_id = str(action.get("application_id") or "").strip()
            if application_id:
                actions_by_application[application_id].append(action)

        rows: list[dict[str, Any]] = []
        for application in applications:
            impact = impact_by_application.get(application.id) or {}
            details = impact.get("details") if isinstance(impact.get("details"), dict) else impact
            interview = _score_progress(reviews_by_application.get(application.id, []))
            application_actions = actions_by_application.get(application.id, [])
            completed_actions = sum(
                str(item.get("status") or "") == "done"
                for item in application_actions
            )
            preparation = application_store.get_interview_preparation(
                user_id, application.id
            )
            rows.append(
                {
                    "application_id": application.id,
                    "company": application.company,
                    "role": application.role,
                    "status": application.status,
                    "credentials_identified": int(details.get("credentials_identified") or 0),
                    "terminology_clarified": int(details.get("terminology_clarified") or 0),
                    "unsupported_claims_prevented": int(details.get("unsupported_claims_prevented") or 0),
                    "relevant_experience_recovered": int(details.get("relevant_experience_recovered") or 0),
                    "baseline_alignment_score": _number(details.get("baseline_alignment_score")),
                    "current_alignment_score": _number(details.get("current_alignment_score")),
                    "alignment_improvement": _number(details.get("alignment_improvement")),
                    "verified_resume_ready": bool(details.get("verified_resume_ready")),
                    "interview_preparation_ready": preparation is not None,
                    "mock_interview": interview,
                    "weak_answers_improved": sum(
                        _weak_answers_with_guidance(review)
                        for review in reviews_by_application.get(application.id, [])
                    ),
                    "actions_completed": completed_actions,
                    "actions_total": len(application_actions),
                    "updated_at": str(impact.get("updated_at") or application.updated_at),
                    "live": bool(impact.get("live")),
                }
            )

        all_interview_progress = _score_progress(reviews)
        measured_rows = [
            row
            for row in rows
            if any(
                (
                    row["credentials_identified"],
                    row["terminology_clarified"],
                    row["unsupported_claims_prevented"],
                    row["relevant_experience_recovered"],
                    row["baseline_alignment_score"] is not None,
                    row["current_alignment_score"] is not None,
                    row["mock_interview"]["sessions"],
                    row["actions_total"],
                )
            )
        ]
        alignment_changes = [
            row["alignment_improvement"]
            for row in rows
            if row["alignment_improvement"] is not None
        ]
        summary = {
            "credentials_identified": sum(row["credentials_identified"] for row in rows),
            "terminology_clarified": sum(row["terminology_clarified"] for row in rows),
            "unsupported_claims_prevented": sum(row["unsupported_claims_prevented"] for row in rows),
            "relevant_experience_recovered": sum(row["relevant_experience_recovered"] for row in rows),
            "alignment_improvement": (
                round(sum(alignment_changes) / len(alignment_changes), 1)
                if alignment_changes
                else None
            ),
            "mock_interview_score_improvement": all_interview_progress["improvement"],
            "weak_answers_improved": sum(
                _weak_answers_with_guidance(review) for review in reviews
            ),
            "actions_completed": sum(
                str(item.get("status") or "") == "done" for item in actions
            ),
            "verified_resumes": sum(row["verified_resume_ready"] for row in rows),
            "applications_measured": len(measured_rows),
            "applications_total": len(applications),
            "mock_interviews": len(reviews),
        }

        before_after = self._before_after(summary, rows)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "before_after": before_after,
            "interview_progress": all_interview_progress,
            "applications": rows,
            "unlinked_mock_interviews": len(unlinked_reviews),
            "warnings": warnings,
            "measurement_note": (
                "Only outcomes found in saved Career Translation, resume-report, "
                "mock-interview, and Career Action Plan records are counted. Empty "
                "metrics mean the related workflow has not produced measurable data yet."
            ),
        }

    @staticmethod
    def _before_after(
        summary: dict[str, Any], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        has_resume = bool(summary["verified_resumes"])
        has_interview_prep = any(row["interview_preparation_ready"] for row in rows)
        has_interview_measurement = bool(summary["mock_interviews"])
        before = [
            "International titles, credentials, or terminology may be unexplained.",
            "Relevant evidence may be missing or difficult for a U.S. employer to interpret.",
            "Resume claims and interview answers have not yet been measured against the target role.",
        ]
        after: list[str] = []
        if summary["credentials_identified"] or summary["terminology_clarified"]:
            after.append(
                f"{summary['credentials_identified']} credential explanation(s) and "
                f"{summary['terminology_clarified']} terminology clarification(s) recorded."
            )
        if summary["unsupported_claims_prevented"] or summary["relevant_experience_recovered"]:
            after.append(
                f"{summary['unsupported_claims_prevented']} unsupported claim(s) kept out and "
                f"{summary['relevant_experience_recovered']} relevant experience item(s) recovered."
            )
        if has_resume:
            after.append(
                f"{summary['verified_resumes']} evidence-reviewed final resume(s) ready for application use."
            )
        if has_interview_prep:
            after.append("Personalized interview preparation has been generated from verified evidence.")
        if has_interview_measurement:
            improvement = summary["mock_interview_score_improvement"]
            score_phrase = (
                f" with a {improvement:+.1f}-point score change"
                if improvement is not None
                else ""
            )
            after.append(
                f"{summary['mock_interviews']} mock interview(s) measured{score_phrase}; "
                f"{summary['weak_answers_improved']} weak answer(s) received improved examples."
            )
        if summary["actions_completed"]:
            after.append(
                f"{summary['actions_completed']} application action(s) completed."
            )
        if not after:
            after.append("Complete a Career Translation or resume workflow to establish the first measured outcome.")
        return {"before": before, "after": after}

    @staticmethod
    def _empty_payload(warning: str) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "credentials_identified": 0,
                "terminology_clarified": 0,
                "unsupported_claims_prevented": 0,
                "relevant_experience_recovered": 0,
                "alignment_improvement": None,
                "mock_interview_score_improvement": None,
                "weak_answers_improved": 0,
                "actions_completed": 0,
                "verified_resumes": 0,
                "applications_measured": 0,
                "applications_total": 0,
                "mock_interviews": 0,
            },
            "before_after": {"before": [], "after": []},
            "interview_progress": {
                "sessions": 0,
                "scored_sessions": 0,
                "first_score": None,
                "latest_score": None,
                "improvement": None,
                "trend": [],
            },
            "applications": [],
            "unlinked_mock_interviews": 0,
            "warnings": [warning],
            "measurement_note": warning,
        }
