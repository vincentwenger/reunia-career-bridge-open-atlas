"""Production entry point for the Réunia Career Bridge application."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent
REUNIA_ROOT = ROOT / "products" / "reunia"

# Réunia still uses ``meeting_assistant`` as its top-level package name.
reunia_path = str(REUNIA_ROOT)
if reunia_path not in sys.path:
    sys.path.insert(0, reunia_path)

from meeting_assistant import create_app as create_reunia_app  # noqa: E402


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def create_application(config_name: str | None = None) -> Flask:
    """Create the single Réunia Career Bridge WSGI application."""

    return create_reunia_app(config_name)


app = create_application()
application = app


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "8000")))
    app.run(host=host, port=port, debug=_env_bool("FLASK_DEBUG", default=False))
