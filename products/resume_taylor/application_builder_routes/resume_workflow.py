from __future__ import annotations

from typing import Any

from .resume_workflow_routes import (
    jobs,
    workspace_view,
    workspace_routes,
    configuration_routes,
    profile_routes,
    tailoring_routes,
    confirmation_routes,
    finalization_routes,
    download_routes,
)

_MODULES = (
    jobs,
    workspace_view,
    workspace_routes,
    configuration_routes,
    profile_routes,
    tailoring_routes,
    confirmation_routes,
    finalization_routes,
    download_routes,
)


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register Resume Workflow controllers and services without a giant closure."""

    combined: dict[str, Any] = {}
    working = dict(namespace)
    for module in _MODULES:
        exported = module.exports()
        combined.update(exported)
        working.update(exported)
    for module in _MODULES:
        module.activate(working)
    globals().update(combined)
    return combined
