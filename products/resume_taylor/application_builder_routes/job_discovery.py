from __future__ import annotations

from typing import Any

from .job_discovery_routes import (
    source_support,
    result_query,
    workspace_view,
    workspace_routes,
    source_routes,
    operations,
    operation_routes,
    action_routes,
)

_MODULES = (
    source_support,
    result_query,
    workspace_view,
    workspace_routes,
    source_routes,
    operations,
    operation_routes,
    action_routes,
)


def register(namespace: dict[str, Any]) -> dict[str, Any]:
    """Register the Job Discovery feature from independently testable modules."""

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
