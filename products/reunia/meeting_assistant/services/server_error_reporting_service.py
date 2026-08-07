from __future__ import annotations

import hashlib
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from flask import current_app, g, request, session

_SECRET_REDACTIONS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?key|secret|password|token)"
            r"\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)([?&](?:token|code|key|secret|password|signature|credential)="
            r")[^&\s]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED]"),
)

_FEATURE_PATHS = (
    ("/applications/job-discovery", "job_discovery", "Job Discovery"),
    ("/applications/career-translation", "career_translation", "Baseline Resume"),
    ("/applications/resume", "resume_workflow", "Resume Workflow"),
    ("/applications/reports", "resume_reports", "Resume Reports"),
    ("/applications", "job_applications", "Job Applications"),
    ("/mock-interview", "browser_recorder", "Mock Interview"),
    ("/admin-analytics", "admin_analytics", "Admin Analytics"),
    ("/help-support", "support", "Help & Support"),
)


class ServerErrorReportingService:
    """Best-effort persistence of server failures for administrators.

    Reporting must never replace the original response with another failure, so
    callers should use ``report_safely`` rather than allowing repository errors
    to escape.
    """

    def report_safely(
        self,
        error: BaseException,
        *,
        status_code: int,
        reference_id: str,
        response_summary: str = "",
    ) -> dict[str, str]:
        if bool(getattr(g, "automatic_server_error_reported", False)):
            return {
                "reference_id": reference_id,
                "support_request_id": str(
                    getattr(g, "automatic_server_error_support_request_id", "") or ""
                ),
            }

        g.automatic_server_error_reported = True
        try:
            result = self._report(
                error,
                status_code=status_code,
                reference_id=reference_id,
                response_summary=response_summary,
            )
        except Exception:
            current_app.logger.exception(
                "Could not persist automatic server error report reference=%s",
                reference_id,
            )
            result = {"reference_id": reference_id, "support_request_id": ""}
        g.automatic_server_error_support_request_id = result.get("support_request_id", "")
        return result

    def _report(
        self,
        error: BaseException,
        *,
        status_code: int,
        reference_id: str,
        response_summary: str,
    ) -> dict[str, str]:
        occurred_at = datetime.now(timezone.utc).isoformat()
        user_id = self._user_id()
        email = self._clean(str(session.get("email") or ""), 254)
        full_name = self._clean(str(session.get("full_name") or ""), 160)
        feature, feature_label = self._feature()
        path = self._clean(request.path, 1000)
        method = self._clean(request.method, 16)
        endpoint = self._clean(str(request.endpoint or ""), 240)
        blueprint = self._clean(str(request.blueprint or ""), 160)
        exception_type = self._clean(error.__class__.__name__, 160)
        error_summary = self._clean(str(error) or exception_type, 1200)
        status_text = self._clean(self._status_text(status_code), 120)
        stack_trace = self._stack_trace(error)
        response_detail = self._clean(response_summary, 2000)

        diagnostics = "\n".join(
            part
            for part in (
                "Automatic Career Bridge server error report",
                f"Reference ID: {reference_id}",
                f"Occurred at: {occurred_at}",
                f"HTTP status: {status_code} {status_text}".strip(),
                f"Feature: {feature_label}",
                f"Request: {method} {path}",
                f"Endpoint: {endpoint or 'Unavailable'}",
                f"Blueprint: {blueprint or 'Unavailable'}",
                f"User ID: {user_id or 'Unavailable'}",
                f"User email: {email or 'Unavailable'}",
                f"Exception: {exception_type}: {error_summary}",
                f"Response summary: {response_detail}" if response_detail else "",
                "",
                "Sanitized traceback:",
                stack_trace or "No Python traceback was available for this response.",
            )
            if part != ""
        )
        diagnostics = self._clean(diagnostics, 16000)

        report_user_id = user_id or email or self._anonymous_user_key()
        support_request_id = ""
        try:
            support_request_id = self._store_support_report(
                occurred_at=occurred_at,
                reference_id=reference_id,
                user_id=report_user_id,
                email=email,
                full_name=full_name,
                feature=feature,
                feature_label=feature_label,
                path=path,
                status_code=status_code,
                status_text=status_text,
                error_summary=error_summary,
                diagnostics=diagnostics,
            )
        except Exception:
            current_app.logger.exception(
                "Could not store automatic Support inbox report reference=%s",
                reference_id,
            )
        try:
            self._store_incident(
                occurred_at=occurred_at,
                reference_id=reference_id,
                support_request_id=support_request_id,
                user_id=report_user_id,
                email=email,
                feature=feature,
                path=path,
                method=method,
                endpoint=endpoint,
                blueprint=blueprint,
                status_code=status_code,
                status_text=status_text,
                exception_type=exception_type,
                error_summary=error_summary,
                diagnostics=diagnostics,
            )
        except Exception:
            current_app.logger.exception(
                "Could not store automatic Admin Analytics incident reference=%s",
                reference_id,
            )
        self._clear_admin_cache()
        return {
            "reference_id": reference_id,
            "support_request_id": support_request_id,
        }

    def _store_support_report(
        self,
        *,
        occurred_at: str,
        reference_id: str,
        user_id: str,
        email: str,
        full_name: str,
        feature: str,
        feature_label: str,
        path: str,
        status_code: int,
        status_text: str,
        error_summary: str,
        diagnostics: str,
    ) -> str:
        repository = current_app.extensions.get("support_repository")
        if repository is None:
            return ""

        request_id = self._new_support_request_id()
        repository.create(
            {
                "request_id": request_id,
                "created_at": occurred_at,
                "status": "new",
                "name": full_name or email or user_id or "Réunia user",
                "email": email or (user_id if "@" in user_id else "Unavailable"),
                "topic": "technical",
                "topic_label": "Technical problem",
                "area": feature,
                "area_label": feature_label,
                "subject": self._clean(
                    f"Automatic server error · {feature_label} · {reference_id}",
                    160,
                ),
                "message": diagnostics,
                "source": "automatic_server_error",
                "page_url": path,
                "user_agent": self._clean(request.user_agent.string, 1000),
                "remote_address": self._clean(self._remote_address(), 128),
                "user_id": user_id,
                "reference_id": reference_id,
                "http_status": str(status_code),
                "status_text": status_text,
                "error_summary": error_summary,
            }
        )
        return request_id

    def _store_incident(
        self,
        *,
        occurred_at: str,
        reference_id: str,
        support_request_id: str,
        user_id: str,
        email: str,
        feature: str,
        path: str,
        method: str,
        endpoint: str,
        blueprint: str,
        status_code: int,
        status_text: str,
        exception_type: str,
        error_summary: str,
        diagnostics: str,
    ) -> None:
        repository = current_app.extensions.get("analytics_repository")
        if repository is None:
            return

        incident_user = user_id
        digest = hashlib.sha256(reference_id.encode("utf-8")).hexdigest()
        repository.record_usage_event(
            {
                "session_key": f"usage#server_error#{digest}",
                "record_type": "usage_event",
                "metric": "server_error",
                "user_id": incident_user,
                "source_id": digest,
                "source": "flask_server",
                "feature": feature,
                "stage": "request_handling",
                "http_status": str(status_code),
                "status_text": status_text,
                "reference_id": reference_id,
                "support_request_id": support_request_id,
                "error_summary": error_summary,
                "cause": "An unhandled server-side exception or explicit 5xx response interrupted the request.",
                "exception_type": exception_type,
                "request_method": method,
                "request_path": path,
                "endpoint": endpoint,
                "blueprint": blueprint,
                "technical_details": diagnostics,
                "incident_status": "open",
                "occurred_at": occurred_at,
                "analytics_date": occurred_at[:10],
                "observed_at": int(time.time()),
            }
        )

    @staticmethod
    def _clear_admin_cache() -> None:
        cache = current_app.extensions.get("admin_analytics_cache")
        if cache is None:
            return
        try:
            cache.clear()
        except Exception:
            current_app.logger.exception(
                "Could not clear Admin Analytics cache after automatic error report"
            )

    @staticmethod
    def _user_id() -> str:
        return str(
            getattr(g, "current_user_id", "")
            or getattr(g, "application_owner_id", "")
            or session.get("user_id")
            or session.get("application_owner_id")
            or ""
        ).strip()[:320]

    @staticmethod
    def _feature() -> tuple[str, str]:
        path = str(request.path or "")
        for prefix, feature, label in _FEATURE_PATHS:
            if path.startswith(prefix):
                return feature, label
        blueprint = str(request.blueprint or "").strip()
        if blueprint:
            return blueprint, blueprint.replace("_", " ").title()
        return "other", "Other"

    @staticmethod
    def _status_text(status_code: int) -> str:
        try:
            from http import HTTPStatus

            return HTTPStatus(int(status_code)).phrase
        except (ValueError, TypeError):
            return "Server Error"

    @classmethod
    def _stack_trace(cls, error: BaseException) -> str:
        if error.__traceback__ is None:
            return ""
        formatted = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        return cls._clean(formatted, 12000)

    @staticmethod
    def _remote_address() -> str:
        route = list(request.access_route or [])
        return str(route[0] if route else request.remote_addr or "")

    @staticmethod
    def _new_support_request_id() -> str:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"SUP-{date}-{uuid4().hex[:8].upper()}"

    @staticmethod
    def _anonymous_user_key() -> str:
        raw = f"{request.remote_addr or ''}\0{request.user_agent.string or ''}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"anonymous-{digest}"

    @classmethod
    def _clean(cls, value: Any, maximum: int) -> str:
        text = str(value or "").replace("\x00", "")
        for pattern, replacement in _SECRET_REDACTIONS:
            text = pattern.sub(replacement, text)
        return text.strip()[:maximum]
