#!/usr/bin/env python3
"""Prevent long AI work from moving back into foreground Flask requests."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_ROUTES = (
    ROOT / "products/resume_taylor/application_builder_routes/job_discovery_routes/operation_routes.py"
).read_text(encoding="utf-8")
INTERVIEW_ROUTES = (
    ROOT / "products/resume_taylor/application_builder_routes/interview_preparation.py"
).read_text(encoding="utf-8")
DISCOVERY_JS = (ROOT / "products/resume_taylor/static/app-job-discovery.js").read_text(encoding="utf-8")
INTERVIEW_JS = (ROOT / "products/resume_taylor/static/interview-preparation.js").read_text(encoding="utf-8")
RESUME_TAILORING_ROUTES = (
    ROOT / "products/resume_taylor/application_builder_routes/resume_workflow_routes/tailoring_routes.py"
).read_text(encoding="utf-8")
RESUME_FINAL_ROUTES = (
    ROOT / "products/resume_taylor/application_builder_routes/resume_workflow_routes/finalization_routes.py"
).read_text(encoding="utf-8")
RESUME_JS = (ROOT / "products/resume_taylor/static/resume-async-jobs.js").read_text(encoding="utf-8")
WORKER = (ROOT / "job_discovery/background_worker.py").read_text(encoding="utf-8")


def segment(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def main() -> int:
    failures: list[str] = []
    discovery_route = segment(
        DISCOVERY_ROUTES,
        "@_routes.post('/discovery/assess/pending')",
        "@_routes.get('/discovery/assess/jobs/<job_id>')",
    )
    interview_route = segment(
        INTERVIEW_ROUTES,
        '@application_builder_bp.post("/interview-preparation/generate")',
        '@application_builder_bp.get("/interview-preparation/jobs/<job_id>")',
    )
    resume_start_route = segment(
        RESUME_TAILORING_ROUTES,
        "@_routes.post('/workflow/start')",
        "@_routes.post('/reports/initial')",
    )
    resume_report_routes = RESUME_TAILORING_ROUTES[
        RESUME_TAILORING_ROUTES.index("@_routes.post('/reports/initial')"):
    ]
    resume_final_route = segment(
        RESUME_FINAL_ROUTES,
        "@_routes.post('/workflow/start-final')",
        "@_routes.post('/resume/save/<version>')",
    )
    forbidden = {
        "Job Discovery request": (discovery_route, ("ResumeAI(", "assess_existing_jobs(")),
        "Interview Preparation request": (interview_route, ("ResumeAI(", "create_interview_preparation(")),
        "Resume Workflow start request": (resume_start_route, ("ResumeAI(", ".analyze_job(", ".create_proposal(")),
        "Resume report requests": (resume_report_routes, ("ResumeAI(", "_refresh_initial_resume_report(")),
        "Final Resume request": (resume_final_route, ("ResumeAI(", "apply_suggested_fixes(", "export_resume_pdf(")),
    }
    for label, (source, tokens) in forbidden.items():
        for token in tokens:
            if token in source:
                failures.append(f"{label} performs foreground AI work: {token}")
        queue_tokens = ("_queue_",) if label.startswith(("Resume Workflow", "Resume report", "Final Resume")) else ("AsyncJob.queued", "async_job_store.create", "202")
        for required in queue_tokens:
            if required not in source:
                failures.append(f"{label} is missing queue contract: {required}")

    for required in ("application_object_key", "document_store.put", '"snapshot_key"'):
        if required not in interview_route:
            failures.append(f"Interview Preparation does not externalize large input: {required}")

    for label, source in (("Job Discovery UI", DISCOVERY_JS), ("Interview Preparation UI", INTERVIEW_JS)):
        for required in ("status_url", "pollUntilTerminal", "cancel_url"):
            if required not in source:
                failures.append(f"{label} is missing background-job behavior: {required}")
    for required in ("status_url", "pollResumeJob", "cancel_url", "retry_url"):
        if required not in RESUME_JS:
            failures.append(f"Resume Workflow UI is missing background-job behavior: {required}")

    resume_path_setup = WORKER.find('RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"')
    resume_import = WORKER.find("from resume_tailor.resume_async_jobs import")
    if resume_path_setup < 0 or resume_import < 0 or resume_path_setup > resume_import:
        failures.append("Background worker imports resume_tailor before registering its package path")

    for required in (
        "class AsyncAIWorker",
        "_run_discovery_assessment",
        "_run_interview_preparation",
        "_run_resume_workflow",
        "RESUME_FINAL_OPTIMIZATION",
        "RESUME_EXPORT",
        "--poll",
        "lambda_handler",
        "document_store.get(snapshot_key)",
    ):
        if required not in WORKER:
            failures.append(f"Background worker is missing: {required}")

    if failures:
        print("Async AI architecture check failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("Async AI architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
