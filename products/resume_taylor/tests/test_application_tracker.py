from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from resume_tailor.application_tracker import (
    SQLiteApplicationStore,
    build_application_metrics,
)


def test_application_store_preserves_resume_snapshot_and_owner_isolation():
    store = SQLiteApplicationStore(":memory:")
    created = store.create(
        "owner-a",
        company="Example Bank",
        role="Senior Engineer",
        status="planned",
        resume_version="Final Resume",
        resume_style="professional",
        alignment_score=87.5,
        overall_score=91.0,
        resume_filename="Candidate_Final_Resume.docx",
        resume_bytes=b"docx-bytes",
        resume_fingerprint="abc123",
    )

    assert created.has_resume_snapshot is True
    assert created.resume_bytes == b"docx-bytes"
    assert store.list_for_owner("owner-a") == [created]
    assert store.list_for_owner("owner-b") == []
    assert store.find_snapshot(
        "owner-a",
        resume_fingerprint="abc123",
        company="example bank",
        role="senior engineer",
    ) == created


def test_application_status_and_outcomes_survive_rejection():
    store = SQLiteApplicationStore(":memory:")
    created = store.create(
        "owner",
        company="Example Co",
        role="Product Lead",
        status="interview",
    )
    assert created.screening_received is True
    assert created.interview_received is True

    updated = store.update(
        "owner",
        created.id,
        company=created.company,
        role=created.role,
        job_url="javascript:alert(1)",
        application_date=created.application_date,
        status="rejected",
        screening_received=True,
        interview_received=True,
        offer_received=False,
        notes="Completed two interviews.",
        next_follow_up_date="",
    )

    assert updated is not None
    assert updated.status == "rejected"
    assert updated.screening_received is True
    assert updated.interview_received is True
    assert updated.offer_received is False
    assert updated.job_url == ""


def test_application_metrics_use_submitted_applications_as_denominator():
    store = SQLiteApplicationStore(":memory:")
    store.create("owner", company="A", role="One", status="planned", alignment_score=99)
    store.create("owner", company="B", role="Two", status="screening", alignment_score=75)
    store.create("owner", company="C", role="Three", status="interview", alignment_score=85)
    store.create("owner", company="D", role="Four", status="offer", alignment_score=95)

    metrics = build_application_metrics(store.list_for_owner("owner"))

    assert metrics.tracked == 4
    assert metrics.submitted == 3
    assert metrics.screening_count == 3
    assert metrics.interview_count == 2
    assert metrics.offer_count == 1
    assert metrics.screening_rate == 100.0
    assert metrics.interview_rate == 66.7
    assert metrics.offer_rate == 33.3
    assert metrics.average_interview_alignment == 90.0


def test_application_tracker_templates_and_routes_are_present(project_root):
    environment = Environment(loader=FileSystemLoader(project_root / "templates"))
    environment.get_template("applications.html")

    base = (project_root / "templates" / "base.html").read_text(encoding="utf-8")
    workflow = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    application_template = (project_root / "templates" / "applications.html").read_text(
        encoding="utf-8"
    )
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert ">Applications</a>" in base
    assert "Save as application" in workflow
    assert "Screening-call rate" in application_template
    assert "Download submitted resume" in application_template
    assert '@app.post("/applications/from-final")' in app_source
    assert '@app.post("/applications/<application_id>/update")' in app_source
    assert '@app.get("/applications/<application_id>/resume")' in app_source
