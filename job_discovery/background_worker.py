"""External worker for queued Career Bridge AI jobs.

Run as a separate process/container from Flask/Gunicorn::

    python -m job_discovery.background_worker --poll --interval 5

The worker claims durable jobs with leases. A replacement worker can reclaim a
job after a crashed worker's lease expires, and progress is stored after every
posting so completed work is never repeated.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

# ``resume_tailor`` is stored under ``products/resume_taylor`` in the merged
# repository.  The web application adds that directory to ``sys.path`` while
# creating the Flask app, but this worker imports resume modules before Flask
# startup.  Register the package root here so the standalone worker container
# can start with the same image as the web container.
ROOT = Path(__file__).resolve().parents[1]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"


def _prepend_import_path(path: Path) -> None:
    import_path = str(path)
    while import_path in sys.path:
        sys.path.remove(import_path)
    sys.path.insert(0, import_path)


for import_root in (ROOT, RESUME_TAYLOR_ROOT):
    _prepend_import_path(import_root)

from career_bridge.async_jobs import (
    AsyncJob,
    AsyncJobStatus,
    AsyncJobStore,
    AsyncJobType,
    AsyncWorkerHeartbeat,
    utc_now_iso,
)
from job_discovery.models import EvidenceReference, WorkplaceType
from job_discovery.ranking import CandidateJobProfile
from job_discovery.service import JobDiscoveryService
from job_discovery.storage import DiscoveryStore
from resume_tailor.resume_async_jobs import RESUME_ASYNC_JOB_TYPES, ResumeWorkflowAsyncProcessor


def candidate_profile_payload(profile: CandidateJobProfile) -> dict[str, Any]:
    payload = asdict(profile)
    payload["accepted_workplace_types"] = [
        value.value if isinstance(value, WorkplaceType) else str(value)
        for value in profile.accepted_workplace_types
    ]
    payload["evidence_references"] = [asdict(item) for item in profile.evidence_references]
    return payload


def candidate_profile_from_payload(payload: Mapping[str, Any]) -> CandidateJobProfile:
    values = dict(payload or {})
    values["evidence_references"] = tuple(
        item if isinstance(item, EvidenceReference) else EvidenceReference(**dict(item))
        for item in (values.get("evidence_references") or ())
    )
    values["accepted_workplace_types"] = tuple(values.get("accepted_workplace_types") or ())
    for key in (
        "target_titles",
        "verified_skills",
        "evidence_statements",
        "preferred_locations",
        "preferred_employment_types",
        "preferred_keywords",
        "required_keywords",
        "excluded_terms",
        "excluded_title_terms",
        "security_clearances",
        "licenses_certifications",
    ):
        values[key] = tuple(values.get(key) or ())
    return CandidateJobProfile(**values)


class AsyncAIWorker:
    def __init__(
        self,
        async_store: AsyncJobStore,
        discovery_store: DiscoveryStore,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 600,
        service_factory=None,
        application_store=None,
        document_store=None,
        interview_ai_factory=None,
        interview_restrictor=None,
        workflow_store=None,
        resume_processor_factory=None,
    ) -> None:
        self.async_store = async_store
        self.discovery_store = discovery_store
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.lease_seconds = max(60, int(lease_seconds))
        self.service_factory = service_factory or (
            lambda store: JobDiscoveryService(store=store)
        )
        self.application_store = application_store
        self.document_store = document_store
        self.interview_ai_factory = interview_ai_factory
        self.interview_restrictor = interview_restrictor
        self.workflow_store = workflow_store
        self.resume_processor_factory = resume_processor_factory
        self.started_at = utc_now_iso()
        self._processed_jobs = 0
        self._current_job_id = ""
        self._current_job_type = ""
        self._heartbeat_lock = threading.RLock()

    def heartbeat(self, *, state: str | None = None) -> AsyncWorkerHeartbeat:
        with self._heartbeat_lock:
            resolved_state = state or ("working" if self._current_job_id else "idle")
            heartbeat = AsyncWorkerHeartbeat(
                worker_id=self.worker_id,
                started_at=self.started_at,
                last_heartbeat_at=utc_now_iso(),
                state=resolved_state,
                processed_jobs=self._processed_jobs,
                current_job_id=self._current_job_id,
                current_job_type=self._current_job_type,
            )
        self.async_store.record_worker_heartbeat(heartbeat)
        return heartbeat

    def run_once(self) -> AsyncJob | None:
        self.heartbeat()
        job = self.async_store.claim_next(
            self.worker_id, lease_seconds=self.lease_seconds
        )
        if job is None:
            return None
        with self._heartbeat_lock:
            self._current_job_id = job.id
            self._current_job_type = job.job_type.value
        self.heartbeat(state="working")
        try:
            if job.job_type is AsyncJobType.JOB_DISCOVERY_ASSESSMENT:
                return self._run_discovery_assessment(job)
            if job.job_type is AsyncJobType.INTERVIEW_PREPARATION:
                return self._run_interview_preparation(job)
            if job.job_type in RESUME_ASYNC_JOB_TYPES:
                return self._run_resume_workflow(job)
            return self._finish(
                job,
                status=AsyncJobStatus.FAILED,
                message=f"No worker handler is registered for {job.job_type.value}.",
            )
        except Exception as exc:
            current = self.async_store.get(job.owner_id, job.id) or job
            return self._finish(
                current,
                status=AsyncJobStatus.FAILED,
                message=f"Background processing failed: {exc}",
            )
        finally:
            with self._heartbeat_lock:
                self._processed_jobs += 1
                self._current_job_id = ""
                self._current_job_type = ""
            self.heartbeat(state="idle")

    def drain(self, *, maximum_jobs: int = 100) -> int:
        processed = 0
        while processed < max(1, int(maximum_jobs)):
            job = self.run_once()
            if job is None:
                break
            processed += 1
        return processed

    def _run_discovery_assessment(self, job: AsyncJob) -> AsyncJob:
        profile = candidate_profile_from_payload(
            dict(job.payload.get("candidate_profile") or {})
        )
        items = list(job.payload.get("jobs") or ())
        service = self.service_factory(self.discovery_store)
        current = job

        # attempted_count is a durable cursor. A reclaimed job resumes at the
        # first unfinished posting rather than rerunning completed AI calls.
        for raw in items[current.attempted_count :]:
            latest = self.async_store.get(current.owner_id, current.id) or current
            if latest.cancel_requested:
                return self._finish(
                    latest,
                    status=AsyncJobStatus.CANCELED,
                    message=(
                        f"Stopped after {latest.attempted_count} of "
                        f"{latest.total_count} jobs. Completed assessments were preserved."
                    ),
                )
            current = latest
            source_id = str(dict(raw or {}).get("source_id") or "").strip()
            job_id = str(dict(raw or {}).get("job_id") or "").strip()
            label = str(dict(raw or {}).get("label") or job_id).strip()
            completed_increment = 0
            failure: dict[str, str] | None = None
            discovered = self.discovery_store.get_discovered_job(
                current.owner_id, source_id, job_id
            )
            if discovered is None:
                failure = {
                    "source_id": source_id,
                    "job_id": job_id,
                    "label": label,
                    "message": "The posting is no longer available.",
                }
            else:
                result = service.assess_existing_jobs([discovered], profile)
                if result.ranked_jobs:
                    completed_increment = 1
                if result.analysis_errors:
                    error = result.analysis_errors[0]
                    failure = {
                        "source_id": source_id,
                        "job_id": job_id,
                        "label": label,
                        "message": str(error.message),
                    }
                elif not result.ranked_jobs:
                    failure = {
                        "source_id": source_id,
                        "job_id": job_id,
                        "label": label,
                        "message": "No fit result was produced.",
                    }

            failures = list(current.failed_items)
            if failure is not None:
                failures.append(failure)
            now = datetime.now(timezone.utc)
            updated = replace(
                current,
                attempted_count=current.attempted_count + 1,
                completed_count=current.completed_count + completed_increment,
                failed_items=tuple(failures),
                message=(
                    f"Assessed {current.attempted_count + 1} of {current.total_count} jobs."
                ),
                lease_owner=self.worker_id,
                lease_expires_at=(
                    now + timedelta(seconds=self.lease_seconds)
                ).isoformat(timespec="seconds"),
            )
            current = self.async_store.save(
                updated, expected_revision=current.revision
            )

        final_status = (
            AsyncJobStatus.COMPLETED_WITH_ERRORS
            if current.failed_items
            else AsyncJobStatus.COMPLETED
        )
        message = (
            f"Assessed {current.completed_count} job"
            f"{'s' if current.completed_count != 1 else ''}."
        )
        if current.failed_items:
            message += (
                f" {len(current.failed_items)} could not be assessed and can be retried."
            )
        else:
            message += " The assessment queue is complete."
        return self._finish(current, status=final_status, message=message)

    def _run_interview_preparation(self, job: AsyncJob) -> AsyncJob:
        if self.application_store is None:
            return self._finish(
                job,
                status=AsyncJobStatus.FAILED,
                message="The interview-preparation worker has no application store.",
            )
        from resume_tailor.interview_preparation import (
            VerifiedEvidenceBundle,
            VerifiedEvidenceItem,
            restrict_workspace_to_evidence,
        )
        from resume_tailor.resume_findings import ResumeFindingsSnapshot

        payload = job.payload
        snapshot_key = str(payload.get("snapshot_key") or "").strip()
        if snapshot_key:
            if self.document_store is None:
                return self._finish(
                    job,
                    status=AsyncJobStatus.FAILED,
                    message="The interview-preparation worker has no document store.",
                )
            import json

            snapshot = json.loads(self.document_store.get(snapshot_key).decode("utf-8"))
            if not isinstance(snapshot, dict):
                raise ValueError("The queued interview-preparation input is invalid.")
            payload = snapshot
        evidence = VerifiedEvidenceBundle(
            items=tuple(
                VerifiedEvidenceItem(
                    id=str(item.get("id") or ""),
                    text=str(item.get("text") or ""),
                    source=str(item.get("source") or ""),
                )
                for item in (payload.get("evidence_items") or ())
            ),
            source_label=str(payload.get("evidence_source_label") or "Verified evidence"),
            fingerprint=str(payload.get("evidence_fingerprint") or ""),
        )
        findings = ResumeFindingsSnapshot.model_validate_json(
            str(payload.get("resume_findings_json") or "{}")
        )
        ai_factory = self.interview_ai_factory
        if ai_factory is None:
            from resume_tailor.ai import ResumeAI

            ai_factory = ResumeAI
        ai = ai_factory(
            str(payload.get("model_name") or "gpt-5-mini"),
            reasoning_effort=(
                str(payload.get("reasoning_effort") or "").strip() or None
            ),
        )
        preparation = ai.create_interview_preparation(
            company=str(payload.get("company") or ""),
            role=str(payload.get("role") or ""),
            interview_audience=str(payload.get("interview_audience") or ""),
            job_description=str(payload.get("job_description") or ""),
            evidence=evidence,
            resume_findings=findings,
            career_profile_context=dict(payload.get("career_profile_context") or {}),
        )
        restrictor = self.interview_restrictor or restrict_workspace_to_evidence
        preparation = restrictor(
            preparation,
            evidence.ids,
            submitted_resume_ids=evidence.submitted_resume_ids,
            evidence_by_id={item.id: item.text for item in evidence.items},
        )
        application_id = str(payload.get("application_id") or "")
        self.application_store.save_interview_preparation(
            job.owner_id,
            application_id,
            content_json=preparation.model_dump_json(),
            job_description_fingerprint=str(payload.get("job_description_fingerprint") or ""),
            evidence_fingerprint=evidence.fingerprint,
            evidence_source_label=evidence.source_label,
            evidence_snapshot_json=str(payload.get("evidence_snapshot_json") or "{}"),
            resume_findings_fingerprint=str(payload.get("resume_findings_fingerprint") or ""),
            resume_findings_snapshot_json=findings.model_dump_json(),
            model_name=str(payload.get("model_name") or ""),
        )
        progressed = self.async_store.save(
            replace(
                job,
                attempted_count=1,
                completed_count=1,
                message="Interview preparation generated and saved to the application.",
            ),
            expected_revision=job.revision,
        )
        return self._finish(
            progressed,
            status=AsyncJobStatus.COMPLETED,
            message="Interview preparation generated and saved to the application.",
        )

    def _resume_processor(self) -> ResumeWorkflowAsyncProcessor:
        if self.workflow_store is None or self.document_store is None:
            raise RuntimeError(
                "The resume-workflow worker requires workflow and document stores."
            )
        if self.resume_processor_factory is not None:
            return self.resume_processor_factory(
                workflow_store=self.workflow_store,
                document_store=self.document_store,
                application_store=self.application_store,
                worker_id=self.worker_id,
            )
        from products.resume_taylor import app as builder

        return ResumeWorkflowAsyncProcessor(
            workflow_store=self.workflow_store,
            document_store=self.document_store,
            application_store=self.application_store,
            builder=builder,
            worker_id=self.worker_id,
        )

    def _save_resume_progress(
        self, job: AsyncJob, *, attempted_count: int, message: str
    ) -> AsyncJob:
        current = self.async_store.get(job.owner_id, job.id) or job
        if current.cancel_requested:
            return current
        now = datetime.now(timezone.utc)
        updated = replace(
            current,
            attempted_count=max(current.attempted_count, attempted_count),
            completed_count=max(current.completed_count, attempted_count),
            message=message,
            lease_owner=self.worker_id,
            lease_expires_at=(
                now + timedelta(seconds=self.lease_seconds)
            ).isoformat(timespec="seconds"),
        )
        return self.async_store.save(updated, expected_revision=current.revision)

    def _run_resume_workflow(self, job: AsyncJob) -> AsyncJob:
        processor = self._resume_processor()
        workflow_key, loaded = processor.load(job)
        models = processor.verify_guard(job, loaded.state)
        operation = str(job.payload.get("operation") or "").strip()
        current = job

        def canceled() -> AsyncJob | None:
            latest = self.async_store.get(current.owner_id, current.id) or current
            if latest.cancel_requested:
                return self._finish(
                    latest,
                    status=AsyncJobStatus.CANCELED,
                    message=(
                        "Background resume processing was canceled. Saved completed "
                        "phases were preserved."
                    ),
                )
            return None

        if job.job_type is AsyncJobType.RESUME_BASELINE_TRANSLATION:
            if current.attempted_count < 1:
                processor.translate_baseline(job, loaded.state, models)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=1, message="Baseline Resume language generated."
                )
            stopped = canceled()
            if stopped is not None:
                return stopped
            if current.attempted_count < 2:
                processor.builder._sync_baseline_roles_to_evidence_library(loaded.state)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=2, message="Baseline Resume synchronized to Career Evidence Library."
                )
            return self._finish(
                current,
                status=AsyncJobStatus.COMPLETED,
                message="Baseline Resume generation completed.",
            )

        if job.job_type is AsyncJobType.RESUME_TAILORING:
            if current.attempted_count < 1:
                processor.translate_baseline(job, loaded.state, models)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=1, message="Target-language Baseline Resume ready."
                )
            stopped = canceled()
            if stopped is not None:
                return stopped
            if current.attempted_count < 2:
                analysis, current_input = processor.analyze(job, loaded.state, models)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=2, message="Job requirements analyzed."
                )
            else:
                analysis = loaded.state.analysis
                current_input = processor.builder.input_fingerprint(loaded.state, models)
            stopped = canceled()
            if stopped is not None:
                return stopped
            if current.attempted_count < 3:
                proposal = processor.create_initial_proposal(
                    job, loaded.state, models, analysis, current_input
                )
                if operation == "tailor":
                    processor.apply_tailoring_result(
                        job, loaded.state, analysis, proposal, current_input
                    )
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=3, message="Initial tailoring proposal created."
                )
            stopped = canceled()
            if stopped is not None:
                return stopped
            if current.attempted_count < 4:
                processor.refresh_report(loaded.state, "initial", force=True)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=4, message="Application Baseline Report generated."
                )
            message = (
                "Job analysis, Target-Market Review, and initial tailoring are ready."
                if operation == "tailor"
                else "Application Baseline Report refreshed."
            )
            return self._finish(current, status=AsyncJobStatus.COMPLETED, message=message)

        if job.job_type is AsyncJobType.RESUME_REPORT:
            report_name = str(job.payload.get("report_name") or operation or "").strip()
            if report_name == "initial" and current.attempted_count < 1:
                processor.translate_baseline(job, loaded.state, models)
                analysis, current_input = processor.analyze(job, loaded.state, models)
                processor.create_initial_proposal(
                    job, loaded.state, models, analysis, current_input
                )
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=1, message="Initial report inputs prepared."
                )
            stopped = canceled()
            if stopped is not None:
                return stopped
            target_attempt = max(1, current.total_count)
            if current.attempted_count < target_attempt:
                processor.refresh_report(loaded.state, report_name, force=True)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current,
                    attempted_count=target_attempt,
                    message=f"{report_name.title()} Resume Report generated.",
                )
            return self._finish(
                current,
                status=AsyncJobStatus.COMPLETED,
                message=f"{report_name.title()} Resume Report is ready.",
            )

        if job.job_type is AsyncJobType.RESUME_FINAL_OPTIMIZATION:
            optimization_already_saved = bool(
                loaded.state.workflow_stage == "final"
                and loaded.state.optimization_started_at
                and loaded.state.optimization_status
                not in {"not_started", "queued", "pending"}
                and loaded.state.final_proposal is not None
            )
            if current.attempted_count < 1 and not optimization_already_saved:
                expected_proposal = str(job.payload.get("proposal_fingerprint") or "")
                actual_proposal = (
                    processor.builder._proposal_fingerprint(loaded.state.final_proposal)
                    if loaded.state.final_proposal is not None
                    else ""
                )
                if expected_proposal and expected_proposal != actual_proposal:
                    raise RuntimeError(
                        "The Final Resume changed after optimization was queued. Start the action again."
                    )
            if current.attempted_count < 1:
                processor.run_final_optimization(job, loaded.state, models)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=1, message="Final optimization and evidence review completed."
                )
            stopped = canceled()
            if stopped is not None:
                return stopped
            if current.attempted_count < 2:
                processor.generate_export(loaded.state, include_exact_report=True)
                loaded = processor.save(job, workflow_key, loaded)
                current = self._save_resume_progress(
                    current, attempted_count=2, message="Final Word and PDF exports generated."
                )
            return self._finish(
                current,
                status=AsyncJobStatus.COMPLETED,
                message="Final Resume optimization, evidence review, reports, and exports are ready.",
            )

        if job.job_type is AsyncJobType.RESUME_EXPORT:
            if bool(job.payload.get("refresh_all_reports")):
                if loaded.state.analysis is not None and loaded.state.initial_evidence_proposal is not None:
                    processor.refresh_report(loaded.state, "initial", force=True)
                if (
                    loaded.state.analysis is not None
                    and loaded.state.draft_proposal is not None
                    and loaded.state.confirmation_complete
                ):
                    processor.refresh_report(loaded.state, "draft", force=True)
            processor.generate_export(loaded.state, include_exact_report=True)
            loaded = processor.save(job, workflow_key, loaded)
            current = self._save_resume_progress(
                current, attempted_count=1, message="Final Word and PDF exports generated."
            )
            return self._finish(
                current,
                status=AsyncJobStatus.COMPLETED,
                message=(
                    "Resume Reports and final Word/PDF exports are ready."
                    if bool(job.payload.get("refresh_all_reports"))
                    else "Final Word and PDF exports are ready."
                ),
            )

        return self._finish(
            current,
            status=AsyncJobStatus.FAILED,
            message=f"Unsupported resume operation: {operation}",
        )

    def _finish(
        self, job: AsyncJob, *, status: AsyncJobStatus, message: str
    ) -> AsyncJob:
        updated = replace(
            job,
            status=status,
            message=message,
            completed_at=utc_now_iso(),
            lease_owner="",
            lease_expires_at="",
        )
        try:
            return self.async_store.save(updated, expected_revision=job.revision)
        except Exception:
            latest = self.async_store.get(job.owner_id, job.id)
            if latest is None or latest.status.terminal:
                return latest or updated
            return self.async_store.save(
                replace(
                    latest,
                    status=status,
                    message=message,
                    completed_at=utc_now_iso(),
                    lease_owner="",
                    lease_expires_at="",
                ),
                expected_revision=latest.revision,
            )



class AsyncWorkerHeartbeatLoop:
    """Publish worker liveness while idle and during long AI calls."""

    def __init__(self, worker: AsyncAIWorker, *, interval_seconds: float = 15.0) -> None:
        self.worker = worker
        self.interval_seconds = max(5.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"async-worker-heartbeat-{worker.worker_id[:24]}",
            daemon=True,
        )

    def start(self) -> None:
        self.worker.heartbeat()
        self._thread.start()

    def stop(self, *, final_state: str = "idle") -> None:
        self._stop.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds + 1.0))
        self.worker.heartbeat(state=final_state)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.worker.heartbeat()
            except Exception as exc:
                print(
                    f"WARNING: Could not persist async worker heartbeat: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

def _configure_application_import_paths() -> None:
    reunia_root = ROOT / "products" / "reunia"
    # Insert in reverse precedence because each path is prepended.  The final
    # order must resolve /app/app.py first, then the Resume Taylor and Réunia
    # top-level packages used by that production entry point.
    for path in (reunia_root, RESUME_TAYLOR_ROOT, ROOT):
        _prepend_import_path(path)


def _application():
    _configure_application_import_paths()
    from app import app

    return app


def lambda_handler(event: Mapping[str, Any] | None, _context: Any) -> dict[str, int]:
    """Process a bounded queue batch from a scheduled AWS Lambda invocation."""

    maximum_jobs = max(1, min(100, int(dict(event or {}).get("maximum_jobs") or 25)))
    app = _application()
    with app.app_context():
        worker = AsyncAIWorker(
            app.extensions["career_bridge_async_job_store"],
            app.extensions["career_bridge_job_discovery_store"],
            lease_seconds=int(app.config.get("CAREER_BRIDGE_ASYNC_JOB_LEASE_SECONDS") or 900),
            application_store=app.extensions.get("career_bridge_application_store"),
            document_store=app.extensions.get("career_bridge_document_store"),
            workflow_store=app.extensions.get("career_bridge_workflow_store"),
        )
        heartbeat_loop = AsyncWorkerHeartbeatLoop(
            worker,
            interval_seconds=float(
                app.config.get("CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS")
                or 15
            ),
        )
        heartbeat_loop.start()
        try:
            processed = worker.drain(maximum_jobs=maximum_jobs)
        finally:
            heartbeat_loop.stop()
    return {"processed_jobs": processed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process durable Career Bridge AI jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll", action="store_true", help="Continuously poll for queued jobs")
    parser.add_argument("--interval", type=float, default=5.0, help="Idle polling interval in seconds")
    parser.add_argument("--maximum-jobs", type=int, default=100)
    args = parser.parse_args(argv)
    app = _application()
    with app.app_context():
        worker = AsyncAIWorker(
            app.extensions["career_bridge_async_job_store"],
            app.extensions["career_bridge_job_discovery_store"],
            lease_seconds=int(app.config.get("CAREER_BRIDGE_ASYNC_JOB_LEASE_SECONDS") or 900),
            application_store=app.extensions.get("career_bridge_application_store"),
            document_store=app.extensions.get("career_bridge_document_store"),
            workflow_store=app.extensions.get("career_bridge_workflow_store"),
        )
        heartbeat_loop = AsyncWorkerHeartbeatLoop(
            worker,
            interval_seconds=float(
                app.config.get("CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS")
                or 15
            ),
        )
        heartbeat_loop.start()
        try:
            if args.poll:
                while True:
                    processed = worker.drain(maximum_jobs=args.maximum_jobs)
                    if not processed:
                        time.sleep(max(1.0, args.interval))
            if args.once:
                return 0 if worker.run_once() is not None else 1
            worker.drain(maximum_jobs=args.maximum_jobs)
        finally:
            heartbeat_loop.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
