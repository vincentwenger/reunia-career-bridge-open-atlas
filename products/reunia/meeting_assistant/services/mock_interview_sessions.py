from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module

class MockInterviewSessionMixin:
    """Mock Interview session lifecycle and persistence."""

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI()
        return self._client

    @classmethod
    def interview_types(cls) -> list[dict[str, str]]:
        return [{"value": key, "label": label} for key, label in _INTERVIEW_TYPES.items()]

    def list_application_options(self, user_id: str) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        application_store = current_app.extensions.get("career_bridge_application_store")
        if application_store is not None:
            try:
                applications = application_store.list_for_owner(user_id)
            except Exception:
                current_app.logger.exception(
                    "Could not load Application Builder records for mock interviews"
                )
                applications = []
            readiness_by_application = InterviewReadinessService(
                application_store=application_store,
                transcript_service=self.transcript_service,
            ).build_for_applications(user_id, applications)
            for application in applications:
                application_id = str(getattr(application, "id", "") or "").strip()
                if not application_id:
                    continue
                company = str(getattr(application, "company", "") or "").strip()
                role = str(getattr(application, "role", "") or "").strip()
                status = str(getattr(application, "status", "") or "").strip()
                interview_audience = str(
                    getattr(application, "interview_audience", "") or ""
                ).strip()
                title = " at ".join(value for value in (role, company) if value)
                if not title:
                    title = "Untitled application"
                details = []
                if status:
                    details.append(status.replace("_", " ").title())
                readiness = readiness_by_application.get(application_id)
                if readiness is not None and readiness.score is not None:
                    details.append(f"Interview readiness {readiness.score:.0f}%")
                options.append(
                    {
                        "id": f"builder:{application_id}",
                        "application_id": application_id,
                        "source": "application_builder",
                        "title": title,
                        "purpose": " · ".join(details),
                        "company": company,
                        "role": role,
                        "status": status,
                        "interview_audience": interview_audience,
                    }
                )

        return options

    def create_session(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        interview_type = str(payload.get("interview_type") or "").strip().lower()
        if interview_type not in _INTERVIEW_TYPES:
            raise ValidationError("Choose a supported interview type.")

        question_set: dict[str, Any] = {}
        saved_questions: list[str] = []
        question_set_id = str(payload.get("question_set_id") or "").strip()
        if interview_type == "saved_questions":
            if not question_set_id:
                raise ValidationError("Choose or save an interview question list first.")
            question_set = self.user_service.get_mock_interview_question_set(
                user_id,
                question_set_id,
            )
            saved_questions = [
                str(question or "").strip()
                for question in question_set.get("questions") or []
                if str(question or "").strip()
            ]
            if not saved_questions:
                raise ValidationError("The saved interview question list is empty.")
            question_count = len(saved_questions)
        else:
            try:
                question_count = int(payload.get("question_count") or 5)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Question count must be a number.") from exc
            if question_count < 3 or question_count > 12:
                raise ValidationError("Choose between 3 and 12 interview questions.")

        custom_focus = str(payload.get("custom_focus") or "").strip()[:1000]
        if interview_type == "custom" and not custom_focus:
            raise ValidationError("Describe what the custom practice session should focus on.")

        workspace_id = str(payload.get("application_workspace_id") or "").strip()
        workspace, workspace_context = self._workspace_context(user_id, workspace_id)
        settings = self.user_service.get_settings(user_id)
        language = normalize_language(payload.get("language") or settings.get("language"), default="en")
        candidate_context = self.user_service.get_assistant_context(user_id)

        session_id = f"mock-{uuid4().hex}"
        if saved_questions:
            opening = {
                "question": saved_questions[0],
                "rationale": "First question from your saved interview question list.",
            }
            interview_type_label = f"My questions — {question_set['name']}"
        else:
            opening = self._generate_opening_question(
                user_id=user_id,
                interview_type=interview_type,
                question_count=question_count,
                custom_focus=custom_focus,
                workspace=workspace,
                workspace_context=workspace_context,
                candidate_context=candidate_context,
                language=language,
                model=str(settings.get("aiModel") or current_app.config["DEFAULT_AI_MODEL"]),
            )
            interview_type_label = _INTERVIEW_TYPES[interview_type]

        now = _utc_now()
        session = {
            "job_id": session_id,
            "session_id": session_id,
            "entity_type": "adaptive_mock_interview",
            "user_id": user_id,
            "status": "active",
            "interview_type": interview_type,
            "interview_type_label": interview_type_label,
            "question_mode": "saved_question_set" if saved_questions else "adaptive",
            "question_set_id": question_set_id,
            "question_set_name": str(question_set.get("name") or ""),
            "saved_questions": saved_questions,
            "question_count": question_count,
            "custom_focus": custom_focus,
            "language": language,
            "application_workspace_id": workspace_id,
            "application_workspace": workspace,
            "workspace_context": workspace_context,
            "candidate_context": candidate_context,
            "current_question": opening["question"],
            "current_question_type": "saved_question" if saved_questions else "opening",
            "current_question_rationale": opening.get("rationale", ""),
            "answers": [],
            "skipped_questions": [],
            "created_at": now,
            "updated_at": now,
            "meeting_id": "",
            "review_timestamp": "",
        }
        if self.session_store.get_mock_interview_session(user_id, session_id) is not None:
            raise ExternalServiceError(
                "The mock interview session could not be created."
            )  # pragma: no cover - UUID collision boundary
        self._save_session(user_id, session)

        self._record_event(
            "mock_interview_started",
            user_id,
            event_id=session_id,
            metadata={
                "interview_type": interview_type,
                "question_count": question_count,
                "linked_workspace": bool(workspace_id),
                "saved_question_set": bool(saved_questions),
            },
        )
        return self._public_session(session)

    def submit_answer(
        self,
        user_id: str,
        session_id: str,
        answer_audio: FileStorage | None,
        *,
        language: str = "",
        duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        session = self._read_owned(user_id, session_id)
        if session.get("status") != "active":
            raise ValidationError("This mock interview is not accepting another answer.")
        if not answer_audio or not answer_audio.filename:
            raise ValidationError("Record an answer before continuing.")

        question_number = self._completed_question_count(session) + 1
        if question_number > int(session.get("question_count") or 0):
            raise ValidationError("The selected number of interview questions has already been completed.")

        transcription = self.transcription_service.transcribe_upload(
            answer_audio,
            source="MICROPHONE",
            user_id=user_id,
            reference_id=f"{session_id}-answer-{question_number}",
            language=language or str(session.get("language") or "en"),
        )
        answer_text = str(transcription.get("text") or "").strip()
        if not answer_text:
            raise ValidationError(
                "No reliable speech was detected. Move closer to the microphone and record the answer again."
            )

        normalized_duration = _bounded_float(duration_seconds, None, 0.0, 3600.0)
        evaluation = self._evaluate_and_follow_up(
            user_id=user_id,
            session=session,
            answer_text=answer_text,
            question_number=question_number,
            duration_seconds=normalized_duration,
        )
        total_questions = int(session["question_count"])
        is_last = question_number >= total_questions
        answer_record = {
            "question_number": question_number,
            "question": str(session.get("current_question") or ""),
            "question_type": str(session.get("current_question_type") or "question"),
            "answer": answer_text,
            "transcription_quality": transcription.get("quality") or {},
            "duration_seconds": normalized_duration,
            "observable_delivery": evaluation.get("observable_delivery") or {},
            "evaluation": evaluation["evaluation"],
            "answered_at": _utc_now(),
        }
        answers = list(session.get("answers") or [])
        answers.append(answer_record)
        session["answers"] = answers
        session["updated_at"] = _utc_now()

        if is_last:
            session["status"] = "ready_for_review"
            session["current_question"] = ""
            session["current_question_type"] = ""
            session["current_question_rationale"] = ""
        elif session.get("question_mode") == "saved_question_set":
            saved_questions = list(session.get("saved_questions") or [])
            session["current_question"] = str(saved_questions[question_number])
            session["current_question_type"] = "saved_question"
            session["current_question_rationale"] = (
                "Next question from your saved interview question list."
            )
        else:
            session["current_question"] = evaluation["next_question"]
            session["current_question_type"] = evaluation["next_question_type"]
            session["current_question_rationale"] = evaluation.get("rationale", "")

        self._save_session(user_id, session)
        self._record_event(
            "mock_interview_answer_completed",
            user_id,
            event_id=f"{session_id}-{question_number}",
            metadata={
                "question_number": question_number,
                "interview_type": session.get("interview_type"),
                "score": evaluation["evaluation"].get("score"),
                "challenge_needed": evaluation["evaluation"].get("challenge_needed"),
            },
        )
        return {
            **self._public_session(session),
            "latest_answer": answer_record,
            "complete": is_last,
        }

    def skip_question(self, user_id: str, session_id: str) -> dict[str, Any]:
        session = self._read_owned(user_id, session_id)
        if session.get("status") != "active":
            raise ValidationError("This mock interview is not accepting another response.")

        answers = list(session.get("answers") or [])
        skipped_questions = list(session.get("skipped_questions") or [])
        question_number = len(answers) + len(skipped_questions) + 1
        total_questions = int(session.get("question_count") or 0)
        if question_number > total_questions or not session.get("current_question"):
            raise ValidationError("There is no active interview question to skip.")
        if question_number >= total_questions and not answers:
            raise ValidationError(
                "Answer at least one question before finishing, or discard the mock interview."
            )

        skipped_record = {
            "question_number": question_number,
            "question": str(session.get("current_question") or ""),
            "question_type": str(session.get("current_question_type") or "question"),
            "skipped_at": _utc_now(),
        }
        skipped_questions.append(skipped_record)
        session["skipped_questions"] = skipped_questions
        session["updated_at"] = _utc_now()
        is_last = question_number >= total_questions

        if is_last:
            session["status"] = "ready_for_review"
            session["current_question"] = ""
            session["current_question_type"] = ""
            session["current_question_rationale"] = ""
        elif session.get("question_mode") == "saved_question_set":
            saved_questions = list(session.get("saved_questions") or [])
            session["current_question"] = str(saved_questions[question_number])
            session["current_question_type"] = "saved_question"
            session["current_question_rationale"] = (
                "Next question from your saved interview question list."
            )
        else:
            session["current_question"] = self._question_after_skip(session, question_number)
            session["current_question_type"] = "new_topic"
            session["current_question_rationale"] = (
                "You skipped the previous question, so the interviewer moved to another competency."
            )

        self._save_session(user_id, session)
        self._record_event(
            "mock_interview_question_skipped",
            user_id,
            event_id=f"{session_id}-{question_number}",
            metadata={
                "question_number": question_number,
                "interview_type": session.get("interview_type"),
            },
        )
        return {
            **self._public_session(session),
            "latest_skipped_question": skipped_record,
            "complete": is_last,
        }

    @staticmethod
    def _completed_question_count(session: dict[str, Any]) -> int:
        return len(session.get("answers") or []) + len(session.get("skipped_questions") or [])

    def _question_after_skip(self, session: dict[str, Any], question_number: int) -> str:
        interview_type = str(session.get("interview_type") or "custom")
        candidates = list(_SKIP_QUESTIONS.get(interview_type) or _SKIP_QUESTIONS["custom"])
        asked = {
            str(item.get("question") or "").strip().casefold()
            for item in [
                *(session.get("answers") or []),
                *(session.get("skipped_questions") or []),
            ]
            if isinstance(item, dict) and str(item.get("question") or "").strip()
        }
        for offset in range(len(candidates)):
            candidate = candidates[(question_number + offset) % len(candidates)]
            if candidate.casefold() not in asked:
                return candidate
        return "What other experience best demonstrates your readiness for this role?"

    def complete_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        session = self._read_owned(user_id, session_id)
        if session.get("status") == "complete":
            return self._completion_payload(session)
        if session.get("status") not in {"ready_for_review", "processing_review"}:
            raise ValidationError("Complete all selected interview questions before generating the review.")

        answers = list(session.get("answers") or [])
        if not answers:
            raise ValidationError("The mock interview does not contain any answers.")

        meeting_id = str(
            session.get("meeting_id")
            or f"mock-interview-{session_id.removeprefix('mock-')}"
        )
        timestamp = str(session.get("review_timestamp") or _utc_now())

        # Persist the final review key before the AI analysis starts so a page
        # reload or worker restart can safely resume the same completion.
        session["status"] = "processing_review"
        session["meeting_id"] = meeting_id
        session["review_timestamp"] = timestamp
        session["updated_at"] = _utc_now()
        self._save_session(user_id, session)

        if self._review_already_saved(user_id, meeting_id, timestamp):
            session["status"] = "complete"
            session["completed_at"] = _utc_now()
            session["updated_at"] = session["completed_at"]
            self._save_session(user_id, session)
            return self._completion_payload(session)

        transcript = self._build_transcript(session)
        workspace = session.get("application_workspace") or {}
        interview_name = self._interview_name(session)
        payload: dict[str, Any] = {
            "meeting_id": meeting_id,
            "timestamp": timestamp,
            "meeting_name": interview_name,
            "transcript": transcript,
            "raw_transcript": transcript,
            "topics": [
                str(session.get("interview_type_label") or "Mock Interview"),
                "Mock Interview",
            ],
            "transcript_quality": {
                "total_segments": len(answers) * 2,
                "kept_segments": len(answers) * 2,
                "removed_no_speech": 0,
                "removed_low_confidence": 0,
                "removed_repetitions": 0,
            },
        }
        if workspace:
            prepared_context = {
                "prepared_meeting_title": str(workspace.get("title") or ""),
                "prepared_meeting_scheduled_at": str(workspace.get("scheduled_at") or ""),
                "prepared_meeting_participants": list(workspace.get("participants") or []),
                "prepared_meeting_purpose": str(workspace.get("purpose") or ""),
            }
            prepared_context.update(
                {
                    "career_application_id": str(workspace.get("application_id") or ""),
                    "career_application_company": str(workspace.get("company") or ""),
                    "career_application_role": str(workspace.get("role") or ""),
                }
            )
            payload.update(prepared_context)

        try:
            result = self.transcript_service.create(
                user_id,
                payload,
                scorecard_source_override="microphone",
                analysis_override=self._build_interview_review(session),
            )
        except Exception:
            session["status"] = "ready_for_review"
            session["updated_at"] = _utc_now()
            self._save_session(user_id, session)
            raise

        session["status"] = "complete"
        session["meeting_id"] = str(result.get("meeting_id") or meeting_id)
        session["review_timestamp"] = str(result.get("timestamp") or timestamp)
        session["completed_at"] = _utc_now()
        session["updated_at"] = session["completed_at"]
        self._save_session(user_id, session)
        self._record_event(
            "mock_interview_completed",
            user_id,
            event_id=session_id,
            metadata={
                "interview_type": session.get("interview_type"),
                "question_count": len(answers),
                "linked_workspace": bool(session.get("application_workspace_id")),
            },
        )
        return self._completion_payload(session)

    def get_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self._public_session(self._read_owned(user_id, session_id))

    def discard_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        session = self._read_owned(user_id, session_id)
        self.session_store.delete_mock_interview_session(user_id, session_id)
        self._record_event(
            "mock_interview_discarded",
            user_id,
            event_id=session_id,
            metadata={"answered_questions": len(session.get("answers") or [])},
        )
        return {"status": "discarded", "session_id": session_id}

    def _review_already_saved(
        self,
        user_id: str,
        meeting_id: str,
        timestamp: str,
    ) -> bool:
        repository = getattr(self.transcript_service, "repository", None)
        getter = getattr(repository, "get_owned", None)
        if not callable(getter):
            return False
        try:
            getter(user_id, meeting_id, timestamp)
        except ResourceNotFoundError:
            return False
        except Exception:
            current_app.logger.exception(
                "Could not verify whether mock-interview review %s already exists",
                meeting_id,
            )
            return False
        return True

    def _save_session(self, user_id: str, session: dict[str, Any]) -> None:
        session_id = str(session.get("session_id") or session.get("job_id") or "").strip()
        if not session_id:
            raise ExternalServiceError("The mock interview session could not be saved.")
        workspace = session.get("application_workspace") or {}
        application_id = str(workspace.get("application_id") or "").strip()
        if not application_id:
            workspace_id = str(session.get("application_workspace_id") or "").strip()
            source, separator, raw_id = workspace_id.partition(":")
            if not separator:
                application_id = workspace_id
            elif source == "builder":
                application_id = raw_id
        self.session_store.save_mock_interview_session(
            user_id,
            session_id,
            application_id=application_id,
            payload_json=json.dumps(
                session,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        )

    def _read_owned(self, user_id: str, session_id: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        if not normalized.startswith("mock-"):
            raise ResourceNotFoundError("Mock interview session not found.")
        try:
            record = self.session_store.get_mock_interview_session(user_id, normalized)
        except Exception as exc:
            raise ResourceNotFoundError("Mock interview session not found.") from exc
        if not record:
            raise ResourceNotFoundError("Mock interview session not found.")
        session = record.get("payload")
        if not isinstance(session, dict):
            raise ResourceNotFoundError("Mock interview session not found.")
        if session.get("entity_type") != "adaptive_mock_interview" or str(session.get("user_id")) != str(user_id):
            raise ResourceNotFoundError("Mock interview session not found.")
        return session


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
