"""Réunia Flask application factory."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
from pathlib import Path

from flask import Flask, g, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from meeting_assistant.config import config_by_name
from meeting_assistant.i18n import normalize_language, supported_language
from meeting_assistant.extensions import init_extensions
from meeting_assistant.utils.admin import current_session_is_admin
from meeting_assistant.utils.csrf import init_csrf
from meeting_assistant.utils.error_handlers import register_error_handlers

_STATIC_FINGERPRINT_CONTENT_LIMIT = 1024 * 1024


def create_app(config_name: str | None = None) -> Flask:
    project_root = Path(__file__).resolve().parent.parent

    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )

    selected_config = config_name or _environment_name()
    config_class = config_by_name.get(selected_config, config_by_name["development"])
    app.config.from_object(config_class)
    if selected_config == "production":
        _validate_production_configuration(app)
    else:
        _validate_dynamodb_table_configuration(app)

    trusted_proxy_hops = max(0, int(app.config.get("TRUSTED_PROXY_HOPS", 0)))
    if trusted_proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_proxy_hops,
            x_proto=trusted_proxy_hops,
            x_host=trusted_proxy_hops,
        )

    app.config["STATIC_ASSET_VERSION"] = (
        os.getenv("STATIC_ASSET_VERSION", "").strip()
        or _static_asset_fingerprint(Path(app.static_folder))
    )
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    app.jinja_env.globals["static_asset"] = lambda filename: url_for(
        "static",
        filename=filename,
        v=app.config["STATIC_ASSET_VERSION"],
    )

    @app.before_request
    def create_security_nonce():
        g.csp_nonce = secrets.token_urlsafe(18)
        supplied_request_id = str(request.headers.get("X-Request-ID") or "").strip()
        if supplied_request_id and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", supplied_request_id):
            g.request_id = supplied_request_id
        else:
            g.request_id = f"REQ-{secrets.token_hex(6).upper()}"

    @app.before_request
    def apply_public_language_parameter():
        """Apply ``?lang=en|fr`` while a visitor is signed out."""
        if session.get("user_id"):
            return

        requested_language = supported_language(request.args.get("lang"))
        if requested_language:
            session["language"] = requested_language

    @app.context_processor
    def inject_application_language():
        return {
            "app_language": normalize_language(session.get("language"), default="en"),
            "is_admin_session": current_session_is_admin(),
            "live_interview_assistance_enabled": bool(
                session.get("live_interview_assistance_enabled")
                or current_session_is_admin()
            ),
            "analytics_heartbeat_seconds": int(
                app.config.get("ANALYTICS_HEARTBEAT_SECONDS", 30)
            ),
            "csp_nonce": getattr(g, "csp_nonce", ""),
        }

    init_csrf(app)
    init_extensions(app)
    register_blueprints(app)
    register_application_builder(app, project_root)
    register_error_handlers(app)
    register_legacy_endpoint_aliases(app)
    register_response_headers(app)

    return app



def _validate_dynamodb_table_configuration(app: Flask) -> None:
    """Require explicit table names for every active DynamoDB repository."""
    if app.testing:
        return

    required = ["USERS_TABLE_NAME", "TRANSCRIPTS_TABLE_NAME"]
    conditional_requirements = (
        ("ACTIONS_STORAGE_BACKEND", "ACTIONS_TABLE_NAME"),
        ("ANALYTICS_STORAGE_BACKEND", "ANALYTICS_TABLE_NAME"),
        ("MEETING_SHARES_STORAGE_BACKEND", "MEETING_SHARES_TABLE_NAME"),
        ("LIVE_QA_STORAGE_BACKEND", "LIVE_QA_TABLE_NAME"),
        ("SUPPORT_STORAGE_BACKEND", "SUPPORT_REQUESTS_TABLE_NAME"),
        ("KNOWLEDGE_STORAGE_BACKEND", "KNOWLEDGE_TABLE_NAME"),
        (
            "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND",
            "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME",
        ),
        (
            "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND",
            "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME",
        ),
        (
            "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND",
            "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME",
        ),
    )
    required.extend(
        table_variable
        for backend_variable, table_variable in conditional_requirements
        if str(app.config.get(backend_variable) or "").strip().lower() == "dynamodb"
    )

    missing = [
        variable
        for variable in required
        if not str(app.config.get(variable) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required DynamoDB table environment variable(s): "
            + ", ".join(missing)
            + ". Table names must be configured explicitly; the application "
            "does not generate fallback table names."
        )


def _configuration_flag(value: object) -> bool:
    """Return whether a configuration value explicitly enables a safety override."""

    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _validate_career_bridge_production_storage(app: Flask) -> None:
    """Reject ephemeral Application Builder persistence in production by default."""

    required_backends = {
        "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND": "dynamodb",
        "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND": "dynamodb",
        "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND": "dynamodb",
        "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3",
    }
    required_resources = (
        "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME",
        "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME",
        "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME",
        "CAREER_BRIDGE_DOCUMENTS_BUCKET",
    )
    invalid_backends = [
        f"{key}={app.config.get(key)!r} (expected {expected!r})"
        for key, expected in required_backends.items()
        if str(app.config.get(key) or "").strip().casefold() != expected
    ]
    missing_resources = [
        key for key in required_resources
        if not str(app.config.get(key) or "").strip()
    ]
    if not invalid_backends and not missing_resources:
        return

    override_key = "CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION"
    details: list[str] = []
    if invalid_backends:
        details.append("unsafe backends: " + "; ".join(invalid_backends))
    if missing_resources:
        details.append("missing resources: " + ", ".join(missing_resources))
    rendered_details = "; ".join(details)

    if _configuration_flag(app.config.get(override_key)):
        logger = getattr(app, "logger", None)
        if logger is not None:
            logger.warning(
                "UNSAFE CAREER BRIDGE DEMO STORAGE OVERRIDE ENABLED: %s. "
                "This deployment is demo-only, may lose records during container "
                "replacement, and must remain at one worker and one node.",
                rendered_details,
            )
        return

    raise RuntimeError(
        "Unsafe Career Bridge production persistence configuration: "
        + rendered_details
        + ". Production requires DynamoDB application/workflow storage and S3 "
        "document storage with explicit table and bucket names. Set "
        f"{override_key}=true only for an intentional demo deployment that accepts "
        "ephemeral data and single-process limitations."
    )


def _validate_production_configuration(app: Flask) -> None:
    _validate_career_bridge_production_storage(app)
    _validate_dynamodb_table_configuration(app)
    required = (
        "SECRET_KEY",
        "REDIS_URL",
        "KNOWLEDGE_FILES_BUCKET",
        "RECORDER_JOBS_BUCKET",
    )
    missing = [key for key in required if not str(app.config.get(key) or "").strip()]
    if missing:
        raise RuntimeError(
            "Missing required production configuration: " + ", ".join(missing)
        )

    secret = str(app.config["SECRET_KEY"])
    if len(secret) < 32 or secret == "development-only-change-me":
        raise RuntimeError("FLASK_SECRET_KEY must be a unique random value of at least 32 characters.")

    production_backends = {
        "RATE_LIMIT_STORAGE_BACKEND": "redis",
        "ADMIN_ANALYTICS_CACHE_BACKEND": "redis",
        "RECORDER_LIVE_STATE_BACKEND": "redis",
        "RECORDER_JOB_QUEUE_BACKEND": "redis",
        "RECORDER_JOB_STORAGE_BACKEND": "s3",
        "KNOWLEDGE_FILE_STORAGE_BACKEND": "s3",
        "ANALYTICS_STORAGE_BACKEND": "dynamodb",
        "LIVE_QA_STORAGE_BACKEND": "dynamodb",
        "ACTIONS_STORAGE_BACKEND": "dynamodb",
        "SUPPORT_STORAGE_BACKEND": "dynamodb",
        "KNOWLEDGE_STORAGE_BACKEND": "dynamodb",
        "MEETING_SHARES_STORAGE_BACKEND": "dynamodb",
    }
    invalid = [
        f"{key}={app.config.get(key)!r} (expected {expected!r})"
        for key, expected in production_backends.items()
        if str(app.config.get(key) or "").strip().lower() != expected
    ]
    if invalid:
        raise RuntimeError("Unsafe production storage configuration: " + "; ".join(invalid))


def _static_asset_fingerprint(static_root: Path) -> str:
    """Return a stable version without rereading large bundled downloads."""
    digest = hashlib.sha256()
    if not static_root.exists():
        return "missing-static"

    for asset_path in sorted(path for path in static_root.rglob("*") if path.is_file()):
        relative_path = asset_path.relative_to(static_root).as_posix()
        stat = asset_path.stat()
        digest.update(relative_path.encode("utf-8"))
        digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}".encode("ascii"))
        # Content-hash normal web assets so their version changes even on file
        # systems with coarse timestamps. Size and nanosecond mtime are enough
        # for large installers and media files, avoiding expensive startup I/O
        # in every web and worker process.
        if stat.st_size <= _STATIC_FINGERPRINT_CONTENT_LIMIT:
            with asset_path.open("rb") as asset_file:
                for chunk in iter(lambda: asset_file.read(64 * 1024), b""):
                    digest.update(chunk)

    return digest.hexdigest()[:16]


def register_response_headers(app: Flask) -> None:
    """Avoid stale HTML while caching content-versioned static files safely."""

    @app.after_request
    def apply_cache_headers(response):
        nonce = getattr(g, "csp_nonce", "")
        response.headers.setdefault("X-Request-ID", str(getattr(g, "request_id", "")))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join(
                [
                    "default-src 'self'",
                    f"script-src 'self' 'nonce-{nonce}'",
                    "style-src 'self' 'unsafe-inline'",
                    "img-src 'self' data:",
                    "font-src 'self'",
                    "connect-src 'self'",
                    "media-src 'self' blob:",
                    "worker-src 'self' blob:",
                    "object-src 'none'",
                    "base-uri 'self'",
                    "frame-ancestors 'none'",
                    "form-action 'self'",
                ]
            ),
        )
        if app.config.get("PREFERRED_URL_SCHEME") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        if request.path.startswith("/shared/meeting/"):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        if request.endpoint == "static":
            requested_version = request.args.get("v", "")
            current_version = app.config.get("STATIC_ASSET_VERSION", "")
            if requested_version and requested_version == current_version:
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            else:
                response.headers["Cache-Control"] = (
                    "no-cache, no-store, must-revalidate, max-age=0"
                )
            return response

        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response


def _environment_name() -> str:
    value = os.getenv("APP_ENV", "development").strip().lower()
    if value in {"prod", "production"}:
        return "production"
    if value in {"test", "testing"}:
        return "testing"
    return "development"


def register_blueprints(app: Flask) -> None:
    from meeting_assistant.blueprints.actions import actions_bp
    from meeting_assistant.blueprints.admin_analytics import admin_analytics_bp
    from meeting_assistant.blueprints.analytics import analytics_bp
    from meeting_assistant.blueprints.auth import auth_bp
    from meeting_assistant.blueprints.knowledge import knowledge_bp
    from meeting_assistant.blueprints.live_qa import live_qa_bp
    from meeting_assistant.blueprints.main import main_bp
    from meeting_assistant.blueprints.meeting_shares import meeting_shares_bp
    from meeting_assistant.blueprints.recorder import recorder_bp
    from meeting_assistant.blueprints.support import support_bp
    from meeting_assistant.blueprints.transcripts import transcript_bp
    from meeting_assistant.blueprints.user_guide import user_guide_bp
    from meeting_assistant.blueprints.users import users_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_analytics_bp)
    app.register_blueprint(actions_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(live_qa_bp)
    app.register_blueprint(transcript_bp)
    app.register_blueprint(meeting_shares_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(user_guide_bp)
    app.register_blueprint(recorder_bp)

    _validate_document_library_routes(app)


def register_application_builder(app: Flask, project_root: Path) -> None:
    """Register the Application Builder Blueprint on the Réunia application."""

    repository_root = project_root.parent.parent
    resume_taylor_root = repository_root / "products" / "resume_taylor"
    resume_taylor_path = str(resume_taylor_root)
    if resume_taylor_path not in sys.path:
        sys.path.insert(0, resume_taylor_path)

    from products.resume_taylor.app import (
        application_builder_bp,
        application_builder_storage_status,
        init_application_builder,
    )

    app.config.setdefault("CAREER_BRIDGE_REQUIRE_AUTH", True)
    app.config.setdefault("CAREER_BRIDGE_LOGIN_URL", "/login.html")
    app.config.setdefault("CAREER_BRIDGE_HOME_URL", "/app")
    app.config.setdefault(
        "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND",
        os.getenv("CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", "memory")
        .strip()
        .lower(),
    )
    app.config.setdefault(
        "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND",
        os.getenv("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", "sqlite")
        .strip()
        .lower(),
    )

    application_backend = str(
        app.config.get("CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", "sqlite")
    ).strip().lower()
    if (
        application_backend == "sqlite"
        and not str(app.config.get("APPLICATIONS_DB_PATH") or "").strip()
    ):
        configured_database_path = (
            os.getenv("CAREER_BRIDGE_APPLICATIONS_DB")
            or os.getenv("APPLICATIONS_DB_PATH")
            or ""
        ).strip()
        if configured_database_path:
            application_database_path = Path(configured_database_path)
        elif app.testing:
            application_database_path = Path(":memory:")
        else:
            application_database_path = (
                repository_root
                / "instance"
                / "career_bridge_applications.sqlite3"
            )

        if str(application_database_path) != ":memory:":
            application_database_path.parent.mkdir(parents=True, exist_ok=True)
        app.config["APPLICATIONS_DB_PATH"] = str(application_database_path)

    init_application_builder(app)
    app.register_blueprint(application_builder_bp, url_prefix="/applications")

    @app.get("/health")
    def health_check():
        return {
            "status": "ok",
            "services": ["reunia", "application-builder"],
            "application_builder": application_builder_storage_status(),
        }


def _validate_document_library_routes(app: Flask) -> None:
    required = {
        ("/api/knowledge/files", "GET"),
        ("/api/knowledge/files", "POST"),
        ("/api/knowledge/collections", "GET"),
        ("/api/knowledge/collections", "POST"),
        ("/api/knowledge/collections/<collection_id>", "DELETE"),
        ("/api/knowledge/ask", "POST"),
    }
    registered = {
        (rule.rule, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods
    }
    missing = sorted(required - registered)
    if missing:
        missing_text = ", ".join(f"{method} {rule}" for rule, method in missing)
        raise RuntimeError(
            "Document Library API routes were not registered: " + missing_text
        )


def register_legacy_endpoint_aliases(app: Flask) -> None:
    """Keep old `url_for()` endpoint names working while routes use Blueprints."""
    aliases = {
        "view_index": "/app",
        "marketing_page": "/",
        "login_page": "/login.html",
        "handle_login": "/api/login",
        "handle_signup": "/api/signup",
        "handle_logout": "/logout",
        "api_get_user": "/api/user",
        "profile_page": "/profile.html",
        "settings_page": "/settings.html",
        "help_support_page": "/help-support.html",
        "user_guide_page": "/user-guide.html",
        "meeting_recorder_page": "/meeting-recorder",
        "action_center_page": "/action-center.html",
        "admin_analytics_page": "/admin/analytics",
        "update_profile": "/update-profile",
        "handle_update_settings": "/update-settings",
        "transcript.get_dynamodb_transcripts": "/api/transcripts",
    }

    for endpoint, rule in aliases.items():
        app.add_url_rule(rule, endpoint=endpoint, build_only=True)
