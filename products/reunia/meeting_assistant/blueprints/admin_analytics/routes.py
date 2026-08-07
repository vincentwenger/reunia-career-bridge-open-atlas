from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Response, current_app, jsonify, redirect, render_template, request, session
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.utils import secure_filename

from meeting_assistant.blueprints.admin_analytics import admin_analytics_bp
from meeting_assistant.services.admin_analytics_service import (
    ActivityTrackingService,
    AdminAnalyticsService,
    UsageMetricsService,
)
from meeting_assistant.services.admin_support_service import AdminSupportService
from meeting_assistant.utils.admin import admin_api_required, admin_required
from meeting_assistant.utils.exceptions import ExternalServiceError, ValidationError


_ANALYTICS_VISITOR_COOKIE = "reunia_visitor"
_BOT_USER_AGENT_RE = re.compile(
    r"(?:bot|crawler|spider|slurp|preview|headless|monitoring|uptime)",
    re.IGNORECASE,
)


def _analytics_identity() -> tuple[str, str, str | None]:
    serializer = URLSafeSerializer(
        current_app.secret_key,
        salt="reunia-analytics-visitor-v1",
    )
    signed_cookie = str(request.cookies.get(_ANALYTICS_VISITOR_COOKIE) or "")
    visitor_id = ""
    if signed_cookie:
        try:
            visitor_id = str(serializer.loads(signed_cookie) or "")
        except BadSignature:
            visitor_id = ""
    new_cookie = None
    if not re.fullmatch(r"[a-f0-9]{32}", visitor_id):
        visitor_id = uuid.uuid4().hex
        new_cookie = serializer.dumps(visitor_id)

    identity = str(session.get("user_id") or "guest")
    session_id = str(session.get("_analytics_session_id") or "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", session_id)
        or session.get("_analytics_identity") != identity
    ):
        session_id = uuid.uuid4().hex
        session["_analytics_session_id"] = session_id
        session["_analytics_identity"] = identity
    return visitor_id, session_id, new_cookie


def _analytics_rate_limited(visitor_id: str) -> bool:
    limiter = current_app.extensions["rate_limiter"]
    allowed, _ = limiter.hit(
        f"analytics:{request.remote_addr or 'unknown'}:{visitor_id}",
        limit=int(current_app.config.get("ANALYTICS_RATE_LIMIT_COUNT", 180)),
        window_seconds=int(
            current_app.config.get("ANALYTICS_RATE_LIMIT_WINDOW_SECONDS", 3600)
        ),
    )
    return not allowed


def _analytics_country_code() -> str | None:
    """Read coarse geography only from an explicitly trusted proxy header."""
    header_name = str(
        current_app.config.get("ANALYTICS_GEO_COUNTRY_HEADER") or ""
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", header_name):
        return None
    country_code = str(request.headers.get(header_name) or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country_code):
        return None
    if country_code in {"XX", "ZZ"}:
        return None
    return country_code


@admin_analytics_bp.get("/admin/analytics")
@admin_required
def admin_analytics_page():
    return render_template("admin-analytics.html")


@admin_analytics_bp.post("/api/analytics/track")
def track_activity():
    if (
        current_app.config.get("ANALYTICS_IGNORE_BOTS", True)
        and _BOT_USER_AGENT_RE.search(str(request.user_agent.string or ""))
    ):
        return "", 204

    visitor_id, analytics_session_id, new_cookie = _analytics_identity()
    if _analytics_rate_limited(visitor_id):
        return "", 204

    payload = request.get_json(silent=True) or {}
    try:
        ActivityTrackingService().record(
            payload,
            visitor_id=visitor_id,
            session_id=analytics_session_id,
            activity_date=datetime.now(timezone.utc).date().isoformat(),
            country_code=_analytics_country_code(),
        )
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except ClientError as exc:
        current_app.logger.warning("Analytics activity could not be stored: %s", exc)
        return "", 204

    response = Response(status=204)
    if new_cookie:
        response.set_cookie(
            _ANALYTICS_VISITOR_COOKIE,
            new_cookie,
            max_age=2 * 365 * 24 * 60 * 60,
            httponly=True,
            secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", True)),
            samesite="Lax",
        )
    return response


@admin_analytics_bp.post("/api/analytics/event")
def track_product_event():
    user_id = str(session.get("user_id") or "").strip()
    if not user_id:
        return "", 204
    if _analytics_rate_limited(user_id):
        return "", 204
    payload = request.get_json(silent=True) or {}
    try:
        UsageMetricsService().record_product_event(
            payload.get("metric"),
            user_id,
            event_id=payload.get("event_id"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
    except Exception:
        current_app.logger.exception("Product analytics event could not be stored")
    return "", 204


@admin_analytics_bp.get("/api/admin/analytics")
@admin_api_required
def admin_analytics_data():
    dashboard = AdminAnalyticsService().dashboard(request.args.get("days"))
    return jsonify(dashboard)


@admin_analytics_bp.get("/api/admin/analytics/users/<path:user_id>/usage")
@admin_api_required
def admin_user_usage(user_id: str):
    return jsonify({"usage": AdminAnalyticsService().user_usage(user_id)})




@admin_analytics_bp.get("/api/admin/analytics/incidents")
@admin_api_required
def admin_incident_details():
    return jsonify(AdminAnalyticsService().incident_details())


@admin_analytics_bp.get("/api/admin/analytics/repeated-failures")
@admin_api_required
def admin_repeated_failure_details():
    # Backward-compatible endpoint for older Admin Analytics clients.
    return jsonify(AdminAnalyticsService().repeated_failure_details())


@admin_analytics_bp.get("/api/admin/support-requests")
@admin_api_required
def admin_support_requests():
    return jsonify(AdminSupportService().inbox())


@admin_analytics_bp.get("/api/admin/support-requests/<request_id>")
@admin_api_required
def admin_support_request_detail(request_id: str):
    mark_read = str(request.args.get("mark_read", "true")).strip().lower() != "false"
    return jsonify({"request": AdminSupportService().get(request_id, mark_read=mark_read)})


@admin_analytics_bp.patch("/api/admin/support-requests/<request_id>")
@admin_api_required
def update_admin_support_request(request_id: str):
    payload = request.get_json(silent=True) or {}
    updated = AdminSupportService().set_status(request_id, payload.get("status"))
    return jsonify({"request": updated})


@admin_analytics_bp.get("/api/admin/support-requests/<request_id>/attachment")
@admin_api_required
def download_admin_support_attachment(request_id: str):
    metadata = AdminSupportService().attachment_metadata(request_id)
    filename = secure_filename(metadata["filename"]) or "support-attachment"
    try:
        url = boto3.client(
            "s3",
            region_name=current_app.config["AWS_REGION"],
        ).generate_presigned_url(
            "get_object",
            Params={
                "Bucket": metadata["bucket"],
                "Key": metadata["object_key"],
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError) as exc:
        current_app.logger.exception(
            "Could not create support attachment link for %s", request_id
        )
        raise ExternalServiceError(
            "The attachment could not be opened right now."
        ) from exc
    return redirect(url)


@admin_analytics_bp.get("/api/admin/analytics/users.csv")
@admin_api_required
def export_admin_users_csv():
    dashboard = AdminAnalyticsService().dashboard(request.args.get("days"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name",
        "Email",
        "Last active (UTC)",
        "Activated",
        "Active days",
        "Returned within 7 days",
        "Sessions",
        "Time today (seconds)",
        "Selected period (seconds)",
        "All time (seconds)",
        "Documents",
        "Document storage (MB)",
        "Saved meetings",
        "Average recording length (seconds)",
        "Maximum recording length (seconds)",
        "Minimum recording length (seconds)",
        "Desktop client downloads",
        "Desktop client uses",
        "Actions",
        "Completed actions",
        "Overdue actions",
        "Recorded failures",
        "AI requests",
        "AI requests without cost data",
        "Estimated AI cost (USD)",
    ])
    for user in dashboard["users"]:
        writer.writerow([
            _safe_csv(user.get("full_name")),
            _safe_csv(user.get("email")),
            _format_csv_datetime(user.get("last_active")),
            "yes" if user.get("activated") else "no",
            user.get("active_day_count") or 0,
            "yes" if user.get("returned_within_7_days") else "no",
            user.get("session_count") or 0,
            user.get("today_active_seconds") or 0,
            user.get("period_active_seconds") or 0,
            user.get("lifetime_active_seconds") or 0,
            user.get("document_count") or 0,
            _format_csv_megabytes(user.get("document_total_bytes")),
            user.get("saved_meeting_count") or 0,
            _optional_csv_number(user.get("average_recording_duration_seconds")),
            _optional_csv_number(user.get("maximum_recording_duration_seconds")),
            _optional_csv_number(user.get("minimum_recording_duration_seconds")),
            user.get("desktop_download_count") or 0,
            user.get("desktop_use_count") or 0,
            user.get("action_count") or 0,
            user.get("completed_action_count") or 0,
            user.get("overdue_action_count") or 0,
            user.get("failure_count") or 0,
            user.get("ai_request_count") or 0,
            user.get("ai_unpriced_request_count") or 0,
            user.get("estimated_ai_cost_usd") or 0,
        ])

    filename = f"reunia-user-activity-{dashboard['period_days']}-days.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _format_csv_datetime(value):
    """Return an unambiguous, spreadsheet-friendly UTC date and time."""
    if value in (None, ""):
        return ""

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        moment = parsed.astimezone(timezone.utc)
    else:
        try:
            moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return str(value)

    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_csv_megabytes(value):
    """Return a spreadsheet-friendly document size in megabytes."""
    try:
        size_bytes = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        size_bytes = 0.0
    return f"{size_bytes / (1024 * 1024):.2f}"


def _optional_csv_number(value):
    """Keep unavailable duration measurements blank instead of implying zero."""
    if value is None:
        return ""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return ""


def _safe_csv(value):
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text
