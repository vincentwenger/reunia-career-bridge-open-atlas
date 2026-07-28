"""WSGI entry point for the Application Builder service.

Réunia and Resume Taylor intentionally keep separate dependency environments.
An ingress proxy exposes this service at ``/application-builder`` while the
shared Flask session cookie provides the authenticated Career Bridge user ID.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
PRODUCT_ROOT = ROOT / "products" / "resume_taylor"
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from app import create_app  # noqa: E402


class ScriptNameMiddleware:
    """Set SCRIPT_NAME when the reverse proxy strips the public URL prefix."""

    def __init__(self, app: Callable, prefix: str) -> None:
        self.app = app
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""

    def __call__(self, environ, start_response):
        environ = environ.copy()
        environ["SCRIPT_NAME"] = self.prefix
        return self.app(environ, start_response)


def create_application_builder_service():
    secret_key = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if not secret_key:
        raise RuntimeError(
            "FLASK_SECRET_KEY is required and must match the Réunia service so "
            "the Application Builder can read the authenticated session."
        )

    database_path = os.environ.get(
        "CAREER_BRIDGE_APPLICATIONS_DB",
        str(PRODUCT_ROOT / "instance" / "career_bridge_applications.sqlite3"),
    )
    flask_app = create_app(
        {
            "SECRET_KEY": secret_key,
            "SESSION_COOKIE_NAME": os.environ.get("SESSION_COOKIE_NAME", "session"),
            "SESSION_COOKIE_PATH": "/",
            "CAREER_BRIDGE_REQUIRE_AUTH": True,
            "CAREER_BRIDGE_LOGIN_URL": os.environ.get(
                "CAREER_BRIDGE_LOGIN_URL", "/login.html"
            ),
            "CAREER_BRIDGE_HOME_URL": os.environ.get(
                "CAREER_BRIDGE_HOME_URL", "/app"
            ),
            "APPLICATIONS_DB_PATH": database_path,
        }
    )
    prefix = os.environ.get("APPLICATION_BUILDER_PREFIX", "/application-builder")
    flask_app.wsgi_app = ScriptNameMiddleware(flask_app.wsgi_app, prefix)
    return flask_app


application = create_application_builder_service()
app = application

__all__ = ["app", "application", "create_application_builder_service"]
