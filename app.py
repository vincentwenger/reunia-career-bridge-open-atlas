"""Production entry point for the Career Bridge Application Builder.

The original Application Builder lives in ``products/resume_taylor``.  This
root module provides a stable ``app:app`` target for Gunicorn and makes the
service usable either:

* as a standalone Lightsail container at ``/``; or
* behind the Career Bridge reverse proxy at ``/application-builder``.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
PRODUCT_ROOT = ROOT / "products" / "resume_taylor"

# The imported product uses ``resume_tailor`` as a top-level package.
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from products.resume_taylor.app import create_app as create_product_app  # noqa: E402


class HealthCheckMiddleware:
    """Serve a dependency-free health check before Flask authentication runs."""

    def __init__(self, application: Callable) -> None:
        self.application = application

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == "/health":
            body = b"ok\n"
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ],
            )
            return [body]
        return self.application(environ, start_response)


class ScriptNameMiddleware:
    """Restore a public URL prefix after a reverse proxy strips it."""

    def __init__(self, application: Callable, prefix: str) -> None:
        self.application = application
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""

    def __call__(self, environ, start_response):
        if not self.prefix:
            return self.application(environ, start_response)
        forwarded_environ = environ.copy()
        forwarded_environ["SCRIPT_NAME"] = self.prefix
        return self.application(forwarded_environ, start_response)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _secret_key() -> str:
    configured = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if configured:
        return configured

    environment = (os.environ.get("APP_ENV") or "development").strip().casefold()
    if environment in {"production", "prod"}:
        raise RuntimeError(
            "FLASK_SECRET_KEY is required in production. Add it as a Lightsail "
            "container environment variable."
        )
    return secrets.token_hex(32)


def create_application() -> object:
    """Create the Flask application from environment-based deployment settings."""

    database_path = Path(
        os.environ.get("CAREER_BRIDGE_APPLICATIONS_DB")
        or os.environ.get("APPLICATIONS_DB_PATH")
        or str(ROOT / "instance" / "career_bridge_applications.sqlite3")
    )
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)

    require_auth = _env_bool("CAREER_BRIDGE_REQUIRE_AUTH", default=False)
    prefix = os.environ.get("APPLICATION_BUILDER_PREFIX", "").strip()

    flask_app = create_product_app(
        {
            "SECRET_KEY": _secret_key(),
            "SESSION_COOKIE_NAME": os.environ.get("SESSION_COOKIE_NAME", "session"),
            "SESSION_COOKIE_PATH": "/",
            "SESSION_COOKIE_SECURE": _env_bool("FLASK_COOKIE_SECURE", default=False),
            "CAREER_BRIDGE_REQUIRE_AUTH": require_auth,
            "CAREER_BRIDGE_LOGIN_URL": os.environ.get(
                "CAREER_BRIDGE_LOGIN_URL", "/login.html"
            ),
            "CAREER_BRIDGE_HOME_URL": os.environ.get(
                "CAREER_BRIDGE_HOME_URL", "/"
            ),
            "APPLICATIONS_DB_PATH": str(database_path),
        }
    )

    flask_app.wsgi_app = HealthCheckMiddleware(flask_app.wsgi_app)
    if prefix:
        flask_app.wsgi_app = ScriptNameMiddleware(flask_app.wsgi_app, prefix)

    return flask_app


app = create_application()
application = app


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "8000")))
    app.run(host=host, port=port, debug=_env_bool("FLASK_DEBUG", default=False))
