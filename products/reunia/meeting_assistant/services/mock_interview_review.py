from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class MockInterviewReviewMixin:
    """Interview review, transcript, public payload, and analytics construction."""

    def _build_interview_review(self, session: dict[str, Any]) -> dict[str, Any]:
        answers = list(session.get("answers") or [])
        criteria_values: dict[str, list[int]] = {
            key: [] for key in _INTERVIEW_SCORECARD_CRITERIA
        }
        answer_reviews: list[dict[str, Any]] = []
        total_words = 0
        total_duration = 0.0
        duration_samples = 0

        for answer in answers:
            evaluation = answer.get("evaluation") if isinstance(answer.get("evaluation"), dict) else {}
            metrics = _normalize_metric_scores(
                evaluation.get("metrics"),
                {},
                question_type=str(answer.get("question_type") or ""),
                role_context_available=bool(
                    (session.get("workspace_context") or {}).get("target_role")
                    or (session.get("application_workspace") or {}).get("role")
                ),
            )
            for key, score in metrics.items():
                if score is not None:
                    criteria_values[key].append(score)

            observable = answer.get("observable_delivery") if isinstance(answer.get("observable_delivery"), dict) else {}
            word_count = _bounded_int(observable.get("word_count"), len(_WORD_RE.findall(str(answer.get("answer") or ""))), 0, 10000)
            duration = _bounded_float(observable.get("duration_seconds"), None, 0.0, 3600.0)
            pace_wpm = _bounded_float(observable.get("pace_wpm"), None, 0.0, 500.0)
            total_words += word_count
            if duration is not None and duration > 0:
                total_duration += duration
                duration_samples += 1

            answer_reviews.append(
                {
                    "question_number": int(answer.get("question_number") or len(answer_reviews) + 1),
                    "question": str(answer.get("question") or ""),
                    "question_type": str(answer.get("question_type") or "question"),
                    "answer": str(answer.get("answer") or ""),
                    "score": _bounded_int(evaluation.get("score"), 0, 0, 100),
                    "evidence_status": str(evaluation.get("evidence_status") or "partial"),
                    "metrics": metrics,
                    "what_worked": _string_list(evaluation.get("what_worked"), 4, _string_list(evaluation.get("strengths"), 4, [])),
                    "what_was_unclear": _string_list(evaluation.get("what_was_unclear"), 4, _string_list(evaluation.get("improvements"), 4, [])),
                    "evidence_to_strengthen": _string_list(evaluation.get("evidence_to_strengthen"), 4, ["Add only evidence that is confirmed in the Career Evidence Library or the answer itself."]),
                    "better_answer_structure": _string_list(evaluation.get("better_answer_structure"), 6, [
                        "Situation or context",
                        "Your responsibility",
                        "Your specific actions and decisions",
                        "Confirmed result and role connection",
                    ]),
                    "sample_improved_answer": str(evaluation.get("sample_improved_answer") or answer.get("answer") or "").strip()[:4000],
                    "recommended_practice_action": str(evaluation.get("recommended_practice_action") or "Practice this answer again using a clearer structure and one confirmed result.").strip()[:1000],
                    "grounding_status": str(evaluation.get("grounding_status") or "legacy_unverified"),
                    "observable_delivery": {
                        "word_count": word_count,
                        "duration_seconds": duration,
                        "pace_wpm": pace_wpm,
                        "answer_length_band": str(observable.get("answer_length_band") or _answer_length_band(word_count)),
                    },
                }
            )

        criteria: dict[str, dict[str, Any]] = {}
        for key, label in _INTERVIEW_SCORECARD_CRITERIA.items():
            values = criteria_values[key]
            average = round(sum(values) / len(values), 1) if values else None
            criteria[key] = {
                "label": label,
                "score": average,
                "observations": len(values),
                "status": "observed" if values else "not_observed",
                "summary": _criterion_summary(key, average, len(values)),
            }

        available_criteria = [
            item["score"] for item in criteria.values() if item["score"] is not None
        ]
        overall_score = round(sum(available_criteria) / len(available_criteria), 1) if available_criteria else None
        answer_count = len(answers)
        if answer_count >= 5 and total_words >= 250:
            evidence_level = "reliable"
            grade_status = "final"
        elif answer_count >= 3 and total_words >= 100:
            evidence_level = "limited"
            grade_status = "preliminary"
        else:
            evidence_level = "insufficient"
            grade_status = "preliminary" if overall_score is not None else "insufficient"

        strongest = sorted(
            ((key, item["score"]) for key, item in criteria.items() if item["score"] is not None),
            key=lambda item: item[1],
            reverse=True,
        )
        weakest = sorted(
            ((key, item["score"]) for key, item in criteria.items() if item["score"] is not None),
            key=lambda item: item[1],
        )
        strongest_label = _INTERVIEW_SCORECARD_CRITERIA[strongest[0][0]] if strongest else "No criterion"
        priority_label = _INTERVIEW_SCORECARD_CRITERIA[weakest[0][0]] if weakest else "More practice evidence"
        summary = (
            f"Completed a {answer_count}-question {str(session.get('interview_type_label') or 'mock interview').lower()}. "
            f"The strongest observed area was {strongest_label.lower()}, and the primary practice priority is {priority_label.lower()}."
        )

        key_wins = _dedupe_strings(
            item
            for review in answer_reviews
            for item in review.get("what_worked") or []
        )[:6]
        improvement_areas = _dedupe_strings(
            item
            for review in answer_reviews
            for item in (review.get("what_was_unclear") or []) + (review.get("evidence_to_strengthen") or [])
        )[:8]
        action_items = _dedupe_strings(
            review.get("recommended_practice_action")
            for review in answer_reviews
            if review.get("recommended_practice_action")
        )[:8]
        if not any(criteria_values["questions_asked_employer"]):
            action_items.append("Prepare and rehearse two or three role-specific questions to ask the employer.")
        action_items = _dedupe_strings(action_items)[:8]

        content_keys = (
            "answer_relevance",
            "use_of_evidence",
            "star_structure",
            "clarity_conciseness",
            "role_alignment",
        )
        delivery_keys = (
            "confidence_of_delivery",
            "handling_follow_up_questions",
            "questions_asked_employer",
        )
        content_scores = [criteria[key]["score"] for key in content_keys if criteria[key]["score"] is not None]
        delivery_scores = [criteria[key]["score"] for key in delivery_keys if criteria[key]["score"] is not None]
        content_average = round(sum(content_scores) / len(content_scores), 1) if content_scores else None
        delivery_average = round(sum(delivery_scores) / len(delivery_scores), 1) if delivery_scores else None
        overall_pace = round(total_words / (total_duration / 60.0), 1) if total_duration > 0 else None

        scorecard_evidence = {
            "overall_grade_status": grade_status,
            "overall_evidence_level": evidence_level,
            "content_grade_status": grade_status,
            "content_evidence_level": evidence_level,
            "form_grade_status": grade_status,
            "form_evidence_level": evidence_level,
            "analyzed_word_count": total_words,
            "substantive_response_count": answer_count,
            "summary": (
                f"Based on {answer_count} recorded answers and {total_words} transcribed candidate words. "
                "Criteria that were not observable are shown as Not observed and are excluded from the overall score."
            ),
        }

        legacy_content_grades = [
            {
                "question": review["question"],
                "answer": review["answer"],
                "relevance_analysis": " ".join(review["what_was_unclear"] or review["what_worked"]),
                "grade": _score_to_grade(review["score"]),
            }
            for review in answer_reviews
        ]

        return {
            "meeting_name": self._interview_name(session),
            "summary": summary,
            "topics": [str(session.get("interview_type_label") or "Mock Interview"), "Mock Interview"],
            "action_items": action_items,
            "open_questions": [],
            "key_wins": key_wins,
            "improvement_areas": improvement_areas,
            "scorecard_type": "interview",
            "interview_scorecard_version": 1,
            "interview_scorecard": {
                "overall_score": overall_score,
                "criteria": criteria,
                "evidence_level": evidence_level,
                "grade_status": grade_status,
                "evidence_summary": scorecard_evidence["summary"],
                "observable_communication": {
                    "answer_count": answer_count,
                    "word_count": total_words,
                    "recorded_duration_seconds": round(total_duration, 1) if duration_samples else None,
                    "pace_wpm": overall_pace,
                    "average_answer_words": round(total_words / answer_count, 1) if answer_count else None,
                },
                "safety_note": _INTERVIEW_SCORECARD_SAFETY_NOTE,
            },
            "interview_answer_reviews": answer_reviews,
            "scorecard_source": "microphone",
            "content_grades": legacy_content_grades,
            "form_metrics": {
                "pace_wpm": overall_pace,
                "overall_assessment": _INTERVIEW_SCORECARD_SAFETY_NOTE,
                "grade_status": grade_status,
            },
            "scorecard_evidence": scorecard_evidence,
            "scorecard_status": grade_status,
            "content_average_score": content_average,
            "form_average_score": delivery_average,
            "final_grade": overall_score,
            "overall_score": overall_score,
        }

    def _build_transcript(self, session: dict[str, Any]) -> str:
        lines = [
            f"Mock interview format: {session.get('interview_type_label')}",
        ]
        if session.get("application_workspace", {}).get("title"):
            lines.append(
                f"Target application: {session['application_workspace']['title']}"
            )
        for answer in session.get("answers") or []:
            lines.append(f"[INTERVIEWER] {answer.get('question')}")
            lines.append(f"[MICROPHONE] {answer.get('answer')}")
        return "\n".join(lines).strip()

    def _interview_name(self, session: dict[str, Any]) -> str:
        label = str(session.get("interview_type_label") or "Mock Interview")
        workspace_title = str((session.get("application_workspace") or {}).get("title") or "").strip()
        return f"{label} — {workspace_title}" if workspace_title else label

    def _public_session(self, session: dict[str, Any]) -> dict[str, Any]:
        answers = list(session.get("answers") or [])
        skipped_questions = list(session.get("skipped_questions") or [])
        completed_question_count = len(answers) + len(skipped_questions)
        return {
            "session_id": str(session.get("session_id") or session.get("job_id") or ""),
            "status": str(session.get("status") or ""),
            "interview_type": str(session.get("interview_type") or ""),
            "interview_type_label": str(session.get("interview_type_label") or ""),
            "question_mode": str(session.get("question_mode") or "adaptive"),
            "question_set_id": str(session.get("question_set_id") or ""),
            "question_set_name": str(session.get("question_set_name") or ""),
            "question_count": int(session.get("question_count") or 0),
            "answered_count": len(answers),
            "skipped_count": len(skipped_questions),
            "completed_question_count": completed_question_count,
            "current_question_number": min(completed_question_count + 1, int(session.get("question_count") or 0)),
            "current_question": str(session.get("current_question") or ""),
            "current_question_type": str(session.get("current_question_type") or ""),
            "current_question_rationale": str(session.get("current_question_rationale") or ""),
            "application_workspace": session.get("application_workspace") or {},
            "answers": answers,
            "skipped_questions": skipped_questions,
            "created_at": str(session.get("created_at") or ""),
            "updated_at": str(session.get("updated_at") or ""),
            "meeting_id": str(session.get("meeting_id") or ""),
            "review_timestamp": str(session.get("review_timestamp") or ""),
        }

    def _completion_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._public_session(session),
            "complete": True,
            "message": "Mock interview completed and review generated.",
            "review_url": "/interview-review",
        }

    def _record_event(
        self,
        metric: str,
        user_id: str,
        *,
        event_id: str,
        metadata: dict[str, Any],
    ) -> None:
        try:
            UsageMetricsService().record_product_event(
                metric,
                user_id,
                event_id=event_id,
                metadata=metadata,
            )
        except Exception:
            current_app.logger.exception("Could not record %s analytics", metric)

    @staticmethod
    def _localized_fallback(text: str, language: str) -> str:
        if language != "fr":
            return text
        translations = {
            _DEFAULT_OPENINGS["recruiter_screening"]: "Présentez-vous et expliquez pourquoi cette opportunité représente une excellente prochaine étape pour vous.",
            _DEFAULT_OPENINGS["hiring_manager"]: "Présentez-moi l’expérience qui vous prépare le mieux à réussir dans ce poste.",
            _DEFAULT_OPENINGS["behavioral"]: "Parlez-moi d’une situation professionnelle difficile et de la façon dont vous l’avez gérée.",
            _DEFAULT_OPENINGS["technical"]: "Décrivez le problème technique le plus pertinent que vous avez résolu et les décisions que vous avez prises.",
            _DEFAULT_OPENINGS["final"]: "Pourquoi êtes-vous la bonne personne pour ce poste et que chercheriez-vous à accomplir en premier ?",
            _DEFAULT_OPENINGS["custom"]: "Que souhaitez-vous que l’intervieweur comprenne en premier sur votre adéquation avec cette opportunité ?",
            _DEFAULT_OPENINGS["saved_questions"]: "Commencez par la première question de votre liste d’entretien enregistrée.",
        }
        return translations.get(text, text)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
