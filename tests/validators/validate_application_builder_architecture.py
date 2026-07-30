"""Dependency-free structural validation for the unified Flask architecture.

This complements the runtime suite in tests/integration/test_application_builder.py.
Run from the repository root:

    python tests/validators/validate_application_builder_architecture.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "app.py"
REUNIA_FACTORY = ROOT / "products/reunia/meeting_assistant/__init__.py"
BUILDER = ROOT / "products/resume_taylor/app.py"
BUILDER_TEMPLATES = ROOT / "products/resume_taylor/templates/application_builder"
CSRF = ROOT / "products/reunia/meeting_assistant/utils/csrf.py"
ERROR_HANDLERS = ROOT / "products/reunia/meeting_assistant/utils/error_handlers.py"
ERROR_TEMPLATE = ROOT / "products/reunia/templates/error.html"


class ValidationFailure(RuntimeError):
    pass


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def literal_url_for_endpoints(text: str) -> set[str]:
    return set(re.findall(r"url_for\(\s*['\"]([^'\"]+)", text))


def validate_single_application_and_error_registration() -> None:
    entrypoint = read(ENTRYPOINT)
    factory = read(REUNIA_FACTORY)
    builder = read(BUILDER)

    forbidden = (
        "DispatcherMiddleware",
        "career_bridge_builder_app",
        "SCRIPT_NAME",
        "create_builder_app",
    )
    combined = entrypoint + factory + builder
    require(
        not any(item in combined for item in forbidden),
        "Legacy split-application WSGI artifacts remain.",
    )
    require(
        "return create_reunia_app(config_name)" in entrypoint,
        "The production factory does not directly return the Réunia app.",
    )
    require(
        "application_builder_bp = Blueprint(" in builder,
        "The Application Builder is not declared as a Blueprint.",
    )
    require(
        "app.register_blueprint(application_builder_bp, url_prefix=\"/applications\")"
        in factory,
        "The Builder Blueprint is not registered on the Réunia app.",
    )
    require(
        factory.index("register_application_builder(app, project_root)")
        < factory.index("register_error_handlers(app)"),
        "Centralized error handlers are not registered on the unified app.",
    )
    module = ast.parse(builder)
    import_time_app_assignments = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in {"app", "application"}
            for target in node.targets
        )
    ]
    require(
        not import_time_app_assignments,
        "The Builder still creates an application object at import time.",
    )


def validate_recovery_pages() -> None:
    handlers = read(ERROR_HANDLERS)
    template = read(ERROR_TEMPLATE)
    require('@app.errorhandler(404)' in handlers, "Centralized 404 handler missing.")
    require(
        '@app.errorhandler(Exception)' in handlers,
        "Centralized unexpected-exception handler missing.",
    )
    for text in (
        "Page Not Found",
        "System Error",
        "Open Help & Support",
        "error_reference_id",
    ):
        require(
            text in handlers or text in template,
            f"Recovery page content is missing: {text}",
        )


def validate_shared_csrf() -> None:
    csrf = read(CSRF)
    builder = read(BUILDER)
    require('_SESSION_KEY = "_csrf_token"' in csrf, "Réunia CSRF session key changed.")
    require(
        'app.jinja_env.globals["csrf_token"] = generate_csrf_token' in csrf,
        "Shared csrf_token() helper is not registered globally.",
    )
    legacy_patterns = (
        'session["csrf_token"]',
        "session['csrf_token']",
        "hmac.compare_digest",
    )
    require(
        not any(pattern in builder for pattern in legacy_patterns),
        "Builder-specific CSRF validation remains.",
    )

    missing: list[str] = []
    form_pattern = re.compile(
        r"<form\b[^>]*method=[\"']post[\"'][^>]*>(.*?)</form>",
        re.IGNORECASE | re.DOTALL,
    )
    for template_path in BUILDER_TEMPLATES.rglob("*.html"):
        template = read(template_path)
        for index, match in enumerate(form_pattern.finditer(template), start=1):
            if "csrf_token" not in match.group(1):
                missing.append(f"{template_path.relative_to(ROOT)} form #{index}")
    require(not missing, "POST forms missing CSRF tokens: " + ", ".join(missing))


def validate_shared_session_configuration() -> None:
    factory = read(REUNIA_FACTORY)
    builder = read(BUILDER)
    require(
        'session.get("user_id")' in builder,
        "Builder authentication does not use the Réunia session user_id.",
    )
    for forbidden in (
        "SESSION_COOKIE_NAME",
        "SESSION_COOKIE_PATH",
        "SECRET_KEY =",
        "secret_key =",
    ):
        require(
            forbidden not in builder,
            f"Builder-specific session configuration remains: {forbidden}",
        )
    require(
        "init_csrf(app)" in factory and "init_extensions(app)" in factory,
        "Shared app extensions are not initialized centrally.",
    )


def validate_static_routing() -> None:
    base_template = read(BUILDER_TEMPLATES / "base.html")
    require(
        "url_for('application_builder.static', filename='styles.css')"
        in base_template,
        "Builder stylesheet does not use the Blueprint static endpoint.",
    )
    require(
        "url_for('application_builder.static', filename='app.js')" in base_template,
        "Builder JavaScript does not use the Blueprint static endpoint.",
    )
    require(
        "static_asset('css/base.css')" in base_template,
        "Shared Réunia assets do not use static_asset().",
    )


def validate_store_initialization() -> None:
    builder = read(BUILDER)
    require(
        'if app.extensions.get("career_bridge_workflow_store") is None:' in builder,
        "Workflow store initialization is not idempotent.",
    )
    require(
        'if app.extensions.get("career_bridge_application_store") is None:' in builder,
        "Application store initialization is not idempotent.",
    )
    require(
        builder.count("create_workflow_store(") == 1,
        "Workflow storage is not constructed through the configured factory.",
    )
    require(
        builder.count("create_application_store(") == 1,
        "Application storage is not constructed through the configured factory.",
    )
    require(
        "store: WorkflowStore" in builder
        and "application_store: ApplicationStore" in builder,
        "Builder store proxies are still annotated as concrete adapters.",
    )


def validate_endpoint_namespace() -> None:
    builder = read(BUILDER)
    endpoints = literal_url_for_endpoints(builder)
    for template_path in BUILDER_TEMPLATES.rglob("*.html"):
        endpoints.update(literal_url_for_endpoints(read(template_path)))

    invalid = sorted(
        endpoint
        for endpoint in endpoints
        if not endpoint.startswith("application_builder.")
        and not endpoint.startswith(".")
    )
    require(
        not invalid,
        "Non-namespaced Builder url_for() endpoints remain: " + ", ".join(invalid),
    )

    module = ast.parse(builder)
    invalid_decorators: list[int] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            owner = decorator.func.value
            if (
                decorator.func.attr in {"get", "post", "put", "patch", "delete", "route"}
                and isinstance(owner, ast.Name)
                and owner.id == "app"
            ):
                invalid_decorators.append(node.lineno)
    require(
        not invalid_decorators,
        "Direct @app route decorators remain at lines: "
        + ", ".join(map(str, invalid_decorators)),
    )


def main() -> int:
    checks = (
        ("single Flask app and shared error handlers", validate_single_application_and_error_registration),
        ("recovery-focused 404 and 500 pages", validate_recovery_pages),
        ("shared CSRF handling", validate_shared_csrf),
        ("shared authentication session", validate_shared_session_configuration),
        ("Blueprint and shared static assets", validate_static_routing),
        ("one store initialization per app", validate_store_initialization),
        ("namespaced Builder endpoints", validate_endpoint_namespace),
    )

    failures = 0
    for label, check in checks:
        try:
            check()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {label}: {exc}")
        else:
            print(f"PASS  {label}")

    if failures:
        print(f"\n{failures} architecture validation check(s) failed.")
        return 1
    print("\nAll 7 architecture validation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
