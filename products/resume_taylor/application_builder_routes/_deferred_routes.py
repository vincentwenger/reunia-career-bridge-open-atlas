from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class _RouteSpec:
    method: str
    rule: str
    endpoint: str
    view_func: Callable[..., Any]
    options: dict[str, Any]


class DeferredRouteRegistry:
    """Collect blueprint routes at import time and bind them after app setup.

    Feature modules can expose top-level, independently testable handlers without
    importing the application blueprint or constructing a giant registration closure.
    """

    def __init__(self) -> None:
        self._specs: list[_RouteSpec] = []
        self._bound_blueprints: set[int] = set()

    def _decorator(self, method: str, rule: str, **options: Any):
        def decorate(view_func: Callable[..., Any]) -> Callable[..., Any]:
            endpoint = str(options.get("endpoint") or view_func.__name__)
            route_options = dict(options)
            route_options.pop("endpoint", None)
            self._specs.append(
                _RouteSpec(method, rule, endpoint, view_func, route_options)
            )
            return view_func
        return decorate

    def get(self, rule: str, **options: Any):
        return self._decorator("GET", rule, **options)

    def post(self, rule: str, **options: Any):
        return self._decorator("POST", rule, **options)

    def bind(self, blueprint: Any) -> None:
        blueprint_key = id(blueprint)
        if blueprint_key in self._bound_blueprints:
            return
        for spec in self._specs:
            blueprint.add_url_rule(
                spec.rule,
                endpoint=spec.endpoint,
                view_func=spec.view_func,
                methods=[spec.method],
                **spec.options,
            )
        self._bound_blueprints.add(blueprint_key)


def module_exports(module_globals: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: module_globals[name] for name in names}


def activate_module(
    module_globals: dict[str, Any], namespace: dict[str, Any], routes: DeferredRouteRegistry
) -> None:
    module_globals.update(namespace)
    routes.bind(namespace["application_builder_bp"])
