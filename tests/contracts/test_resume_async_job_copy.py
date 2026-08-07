from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_resume_async_job_copy_is_short_and_user_friendly():
    template = (
        ROOT
        / "products"
        / "resume_taylor"
        / "templates"
        / "application_builder"
        / "_resume_async_job.html"
    ).read_text(encoding="utf-8")
    javascript = (
        ROOT
        / "products"
        / "resume_taylor"
        / "static"
        / "resume-async-jobs.js"
    ).read_text(encoding="utf-8")
    jobs = (ROOT / "career_bridge" / "async_jobs.py").read_text(encoding="utf-8")

    assert "Preparing your resume" in template
    assert "You can leave this page and come back later." in template
    assert "Your resume is being prepared." in jobs
    assert "In progress" in javascript

    combined = "\n".join((template, javascript, jobs))
    for technical_copy in (
        "Durable background processing",
        "Background worker is processing this job.",
        "The job and completed phases are stored durably.",
    ):
        assert technical_copy not in combined
