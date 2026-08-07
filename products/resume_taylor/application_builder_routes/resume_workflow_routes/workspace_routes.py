from __future__ import annotations

from typing import Any

from .._deferred_routes import DeferredRouteRegistry, activate_module, module_exports

"""Thin Resume Workflow page controller."""

_routes = DeferredRouteRegistry()

@_routes.get("/")
def index():
    return render_resume_workflow_index()


_EXPORT_NAMES = (
    'index',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace, _routes)
