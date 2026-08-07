from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Administrator AI configuration controller."""

_routes = DeferredRouteRegistry()

@_routes.post('/configuration')
def configuration():
    if not bool(session.get("is_admin")):
        abort(403, description="Administrator access is required for AI configuration.")
    current = state()
    try:
        old_models = resolve_models(current)
    except ValueError:
        old_models = ActiveModels("", "", None, None)

    mode = request.form.get("processing_mode", current.processing_mode)
    if mode not in PROCESSING_MODE_ORDER:
        flash("Unknown processing mode.", "error")
        return redirect(url_for("application_builder.index", tab="configuration"))
    current.processing_mode = mode
    current.custom_analysis_tailoring_model = request.form.get(
        "custom_analysis_tailoring_model", current.custom_analysis_tailoring_model
    ).strip()
    current.custom_evidence_review_model = request.form.get(
        "custom_evidence_review_model", current.custom_evidence_review_model
    ).strip()
    analysis_tailoring_effort = request.form.get(
        "custom_analysis_tailoring_reasoning_effort", "automatic"
    )
    evidence_review_effort = request.form.get(
        "custom_evidence_review_reasoning_effort", "automatic"
    )
    current.custom_analysis_tailoring_reasoning_effort = (
        None
        if analysis_tailoring_effort == "automatic"
        else analysis_tailoring_effort
    )
    current.custom_evidence_review_reasoning_effort = (
        None
        if evidence_review_effort == "automatic"
        else evidence_review_effort
    )

    try:
        new_models = resolve_models(current)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("application_builder.index", tab="configuration"))

    old_analysis_tailoring = (
        old_models.analysis_tailoring_model,
        old_models.analysis_tailoring_reasoning_effort,
    )
    new_analysis_tailoring = (
        new_models.analysis_tailoring_model,
        new_models.analysis_tailoring_reasoning_effort,
    )
    old_evidence_review = (
        old_models.evidence_review_model,
        old_models.evidence_review_reasoning_effort,
    )
    new_evidence_review = (
        new_models.evidence_review_model,
        new_models.evidence_review_reasoning_effort,
    )
    if old_analysis_tailoring != new_analysis_tailoring:
        current.clear_results()
        flash("AI configuration updated. Cached analysis and proposals were cleared.", "success")
    elif old_evidence_review != new_evidence_review:
        current.clear_tailoring_results()
        flash(
            "Evidence-review configuration updated. Job-aligned and final resume versions were cleared because Step 3 must be verified again.",
            "success",
        )
    else:
        flash("Configuration saved.", "success")
    return redirect(url_for("application_builder.index", tab="configuration"))


_EXPORT_NAMES = (
    'configuration',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
