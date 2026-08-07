#!/usr/bin/env python3
"""Validate the feature-focused Application Builder route architecture."""

from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_PATH = ROOT / "products" / "resume_taylor" / "app.py"
DOCKERFILE_PATH = ROOT / "Dockerfile"
ROUTE_ROOT = ROOT / "products" / "resume_taylor" / "application_builder_routes"
MODULE_ORDER = (
    "lifecycle",
    "application_context",
    "resume_workflow",
    "career_translation",
    "interview_preparation",
    "job_discovery",
    "applications",
)
EXPECTED_ROUTE_COUNT = 75
MAX_COMPOSITION_ROOT_LINES = 5_000
MAX_FEATURE_MODULE_LINES = 1_000
MAX_ROUTE_REGISTER_LINES = 80


@dataclass(frozen=True)
class RegisteredRoute:
    method: str
    rule: str
    endpoint: str


class FakeBlueprint:
    """Minimal decorator surface used to smoke-test route registration."""

    def __init__(self) -> None:
        self.routes: list[RegisteredRoute] = []
        self.hooks: list[tuple[str, str]] = []

    def _route_decorator(self, method: str, rule: str, **options: Any):
        def decorate(function: Callable[..., Any]):
            endpoint = str(options.get("endpoint") or function.__name__)
            self.routes.append(RegisteredRoute(method, rule, endpoint))
            return function

        return decorate

    def add_url_rule(
        self, rule: str, endpoint: str, view_func: Callable[..., Any], methods: list[str], **options: Any
    ) -> None:
        for method in methods:
            self.routes.append(
                RegisteredRoute(str(method).upper(), rule, endpoint)
            )

    def get(self, rule: str, **options: Any):
        return self._route_decorator("GET", rule, **options)

    def post(self, rule: str, **options: Any):
        return self._route_decorator("POST", rule, **options)

    def put(self, rule: str, **options: Any):
        return self._route_decorator("PUT", rule, **options)

    def delete(self, rule: str, **options: Any):
        return self._route_decorator("DELETE", rule, **options)

    def route(self, rule: str, **options: Any):
        methods = options.get("methods") or ("GET",)

        def decorate(function: Callable[..., Any]):
            endpoint = str(options.get("endpoint") or function.__name__)
            for method in methods:
                self.routes.append(
                    RegisteredRoute(str(method).upper(), rule, endpoint)
                )
            return function

        return decorate

    def before_request(self, function: Callable[..., Any]):
        self.hooks.append(("before_request", function.__name__))
        return function

    def after_request(self, function: Callable[..., Any]):
        self.hooks.append(("after_request", function.__name__))
        return function

    def context_processor(self, function: Callable[..., Any]):
        self.hooks.append(("context_processor", function.__name__))
        return function


def _route_decorators(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            rendered = ast.unparse(decorator)
            if rendered.startswith(("application_builder_bp.", "_routes.")) and any(
                marker in rendered
                for marker in (".route(", ".get(", ".post(", ".put(", ".delete(")
            ):
                routes.append((node.name, rendered))
    return routes


def validate() -> list[str]:
    errors: list[str] = []
    app_source = APP_PATH.read_text(encoding="utf-8")
    app_lines = len(app_source.splitlines())
    if app_lines > MAX_COMPOSITION_ROOT_LINES:
        errors.append(
            f"{APP_PATH.relative_to(ROOT)} has {app_lines} lines; "
            f"limit is {MAX_COMPOSITION_ROOT_LINES}."
        )
    if _route_decorators(APP_PATH):
        errors.append("Application Builder route decorators must not live in app.py.")

    dockerfile_source = DOCKERFILE_PATH.read_text(encoding="utf-8")
    required_docker_copy = (
        "COPY products/resume_taylor/application_builder_routes "
        "./products/resume_taylor/application_builder_routes"
    )
    if required_docker_copy not in dockerfile_source:
        errors.append(
            "Dockerfile must copy products/resume_taylor/application_builder_routes "
            "into the runtime image."
        )

    for module_name in MODULE_ORDER:
        path = ROUTE_ROOT / f"{module_name}.py"
        if not path.exists():
            errors.append(f"Missing route module: {path.relative_to(ROOT)}")
            continue
        source = path.read_text(encoding="utf-8")
        line_count = len(source.splitlines())
        if line_count > MAX_FEATURE_MODULE_LINES:
            errors.append(
                f"{path.relative_to(ROOT)} has {line_count} lines; "
                f"limit is {MAX_FEATURE_MODULE_LINES}."
            )
        if "products.resume_taylor.app" in source:
            errors.append(
                f"{path.relative_to(ROOT)} must not import the composition root."
            )
        tree = ast.parse(source)
        register = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "register"
            ),
            None,
        )
        if register is None:
            errors.append(f"{path.relative_to(ROOT)} does not define register().")
        elif module_name in {"job_discovery", "resume_workflow"}:
            register_lines = register.end_lineno - register.lineno + 1
            if register_lines > MAX_ROUTE_REGISTER_LINES:
                errors.append(
                    f"{path.relative_to(ROOT)} register() has {register_lines} lines; "
                    f"limit is {MAX_ROUTE_REGISTER_LINES}."
                )

    static_routes: list[tuple[str, str]] = []
    for path in ROUTE_ROOT.rglob("*.py"):
        static_routes.extend(_route_decorators(path))
        if path.parent.name in {"job_discovery_routes", "resume_workflow_routes"}:
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > MAX_FEATURE_MODULE_LINES:
                errors.append(
                    f"{path.relative_to(ROOT)} has {line_count} lines; "
                    f"limit is {MAX_FEATURE_MODULE_LINES}."
                )
    if len(static_routes) != EXPECTED_ROUTE_COUNT:
        errors.append(
            f"Expected {EXPECTED_ROUTE_COUNT} Application Builder route decorators; "
            f"found {len(static_routes)}."
        )
    function_names = [name for name, _ in static_routes]
    duplicates = sorted({name for name in function_names if function_names.count(name) > 1})
    if duplicates:
        errors.append(f"Duplicate route function names: {', '.join(duplicates)}")

    blueprint = FakeBlueprint()
    namespace: dict[str, Any] = {
        "application_builder_bp": blueprint,
        "DEFAULT_MAX_POSTING_AGE_DAYS": 30,
    }
    try:
        for module_name in MODULE_ORDER:
            module = importlib.import_module(
                f"products.resume_taylor.application_builder_routes.{module_name}"
            )
            namespace.update(module.register(namespace))
    except Exception as exc:  # pragma: no cover - command reports exact failure
        errors.append(f"Route registration smoke test failed: {type(exc).__name__}: {exc}")
    else:
        if len(blueprint.routes) != EXPECTED_ROUTE_COUNT:
            errors.append(
                f"Registration smoke test expected {EXPECTED_ROUTE_COUNT} routes; "
                f"registered {len(blueprint.routes)}."
            )
        endpoint_names = [route.endpoint for route in blueprint.routes]
        duplicate_endpoints = sorted(
            {name for name in endpoint_names if endpoint_names.count(name) > 1}
        )
        if duplicate_endpoints:
            errors.append(
                "Duplicate registered endpoints: " + ", ".join(duplicate_endpoints)
            )
        expected_hooks = {
            ("before_request", "load_workflow_state"),
            ("after_request", "add_job_discovery_server_timing"),
            ("after_request", "persist_workflow_state"),
            ("context_processor", "inject_common_template_values"),
        }
        missing_hooks = expected_hooks.difference(blueprint.hooks)
        if missing_hooks:
            errors.append(
                "Missing lifecycle hooks: "
                + ", ".join(f"{kind}:{name}" for kind, name in sorted(missing_hooks))
            )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "Application Builder route architecture passed: "
        f"{EXPECTED_ROUTE_COUNT} routes across {len(MODULE_ORDER)} feature modules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
