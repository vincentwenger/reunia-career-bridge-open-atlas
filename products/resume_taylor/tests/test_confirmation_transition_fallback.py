from __future__ import annotations


def _confirmation_route_source(project_root):
    source = (project_root / "app.py").read_text(encoding="utf-8")
    return source.split("def apply_confirmation():", 1)[1].split(
        '@app.post("/confirmation/reopen")', 1
    )[0]


def test_refinement_failure_keeps_confirmed_progress_and_advances(project_root):
    route = _confirmation_route_source(project_root)

    assert "except ResumeAIError as exc:" in route
    assert "refined = proposal_for_refinement.model_copy(deep=True)" in route
    assert "optional AI wording refinement could not complete" in route
    assert 'redirect_stage = "draft"' in route


def test_evidence_review_failure_uses_conservative_fallback(project_root):
    route = _confirmation_route_source(project_root)

    assert "except (ResumeAIError, ValueError) as exc:" in route
    assert "Post-confirmation evidence review failed" in route
    assert "proposal_for_refinement.model_copy(deep=True)" in route
    assert "ensure_confirmed_answers_visible(" in route
    assert "apply_all_until_valid(" in route
    assert "candidate_needed = []" in route
    assert "conservative source-backed wording and deterministic validation" in route


def test_successful_confirmation_clears_saved_form_and_opens_step_three(project_root):
    route = _confirmation_route_source(project_root)

    assert "current.confirmation_draft = {}" in route
    assert "current.confirmation_complete = True" in route
    assert 'url_for("index", tab="tailoring", stage=redirect_stage)' in route
    assert 'redirect_anchor = "#tailored-resume"' in route
