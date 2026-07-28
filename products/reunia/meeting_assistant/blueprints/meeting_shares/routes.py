from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from flask import (
    Response,
    current_app,
    g,
    jsonify,
    make_response,
    render_template,
    request,
    session,
    url_for,
)

from meeting_assistant.blueprints.meeting_shares import meeting_shares_bp
from meeting_assistant.i18n import normalize_language, supported_language
from meeting_assistant.services.meeting_share_service import MeetingShareService
from meeting_assistant.utils.authentication import api_auth_required
from meeting_assistant.utils.exceptions import RateLimitError, ResourceNotFoundError

_UNLOCK_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
_UNLOCK_LOCK = threading.RLock()


@meeting_shares_bp.route(
    "/api/meetings/<string:meeting_id>/shares",
    methods=["GET", "POST"],
)
@api_auth_required
def meeting_shares_collection(meeting_id: str):
    service = MeetingShareService()
    if request.method == "GET":
        timestamp = request.args.get("timestamp")
        return jsonify(service.list_for_meeting(g.current_user_id, meeting_id, timestamp))

    data = request.get_json(silent=True) or {}
    result = service.create(
        g.current_user_id,
        meeting_id,
        data.get("timestamp") or data.get("meeting_timestamp"),
        data,
    )
    return jsonify(result), 201


@meeting_shares_bp.route(
    "/api/meeting-shares/<string:share_id>",
    methods=["PATCH", "DELETE"],
)
@api_auth_required
def manage_meeting_share(share_id: str):
    service = MeetingShareService()
    if request.method == "DELETE":
        return jsonify(service.revoke(g.current_user_id, share_id))
    return jsonify(
        service.update(
            g.current_user_id,
            share_id,
            request.get_json(silent=True) or {},
        )
    )


@meeting_shares_bp.get("/shared/meeting/<string:share_id>")
def public_shared_meeting(share_id: str):
    service = MeetingShareService()
    try:
        record = service.get_public(share_id)
    except ResourceNotFoundError as exc:
        return _public_error(str(exc), 404)
    if service.requires_password(record) and not _is_unlocked(share_id):
        return _public_response(
            _render_shared_meeting(
                share=record,
                snapshot=record.get("snapshot") or {},
                password_required=True,
                password_error="",
                unavailable_message="",
            )
        )

    service.record_access(record)
    return _public_response(
        _render_shared_meeting(
            share=record,
            snapshot=record.get("snapshot") or {},
            password_required=False,
            password_error="",
            unavailable_message="",
        )
    )


@meeting_shares_bp.post("/shared/meeting/<string:share_id>/unlock")
def unlock_shared_meeting(share_id: str):
    _check_unlock_rate_limit(share_id)
    service = MeetingShareService()
    try:
        record = service.get_public(share_id)
    except ResourceNotFoundError as exc:
        return _public_error(str(exc), 404)
    password = request.form.get("password", "")
    if not service.verify_password(record, password):
        return _public_response(
            _render_shared_meeting(
                share=record,
                snapshot=record.get("snapshot") or {},
                password_required=True,
                password_error="The password is incorrect.",
                unavailable_message="",
            ),
            status=401,
        )
    unlocked = set(session.get("unlocked_meeting_shares", []))
    unlocked.add(share_id)
    session["unlocked_meeting_shares"] = sorted(unlocked)[-20:]
    session.modified = True
    service.record_access(record)
    return _public_response(
        _render_shared_meeting(
            share=record,
            snapshot=record.get("snapshot") or {},
            password_required=False,
            password_error="",
            unavailable_message="",
        )
    )


@meeting_shares_bp.get("/shared/meeting/<string:share_id>/download")
def download_shared_meeting(share_id: str):
    service = MeetingShareService()
    try:
        record = service.get_public(share_id)
    except ResourceNotFoundError as exc:
        return _public_error(str(exc), 404)
    if service.requires_password(record) and not _is_unlocked(share_id):
        return _public_response(
            _render_shared_meeting(
                share=record,
                snapshot={},
                password_required=True,
                password_error="Unlock the meeting before downloading it.",
                unavailable_message="",
            ),
            status=401,
        )
    if not bool(record.get("allow_download")):
        return _public_error("Downloads are disabled for this shared meeting.", 403)
    text = service.build_download_text(record, language=_share_language(record))
    safe_name = "shared-meeting.txt"
    response = Response(text, mimetype="text/plain; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return _secure_public_headers(response)


def _render_shared_meeting(
    *,
    share: dict,
    snapshot: dict,
    password_required: bool,
    password_error: str,
    unavailable_message: str,
) -> str:
    share_language = _share_language(share)
    return render_template(
        "shared-meeting.html",
        share=share,
        snapshot=snapshot,
        share_language=share_language,
        password_required=password_required,
        password_error=password_error,
        unavailable_message=unavailable_message,
        social_meta=_build_social_meta(
            snapshot=snapshot,
            password_required=password_required,
            unavailable=bool(unavailable_message),
            language=share_language,
        ),
    )


def _build_social_meta(
    *,
    snapshot: dict,
    password_required: bool,
    unavailable: bool,
    language: str,
) -> dict[str, str]:
    language = normalize_language(language, default="en")
    if language == "fr":
        if unavailable:
            title = "Réunion partagée indisponible | Réunia"
            description = "Ce lien de partage Réunia est indisponible ou a expiré."
        elif password_required:
            title = "Réunion protégée partagée via Réunia"
            description = "Ouvrez une réunion en lecture seule protégée par mot de passe et partagée via Réunia."
        else:
            meeting_name = str(snapshot.get("meeting_name") or "Réunion partagée").strip()
            title = f"{meeting_name} — Réunion partagée"
            description = "Consultez en toute sécurité le résumé d’une réunion partagée en lecture seule via Réunia."
        image_alt = "Aperçu sécurisé d’une réunion partagée via Réunia"
    else:
        if unavailable:
            title = "Shared meeting unavailable | Réunia"
            description = "This Réunia shared link is unavailable or has expired."
        elif password_required:
            title = "Protected meeting shared through Réunia"
            description = "Open a password-protected, read-only meeting shared through Réunia."
        else:
            meeting_name = str(snapshot.get("meeting_name") or "Shared meeting").strip()
            title = f"{meeting_name} — Shared meeting"
            description = "View a read-only meeting summary securely shared through Réunia."
        image_alt = "Réunia secure shared meeting preview"

    asset_version = current_app.config.get("STATIC_ASSET_VERSION", "")
    image_url = url_for(
        "static",
        filename="images/shared-meeting-preview.png",
        v=asset_version,
        _external=True,
    )
    return {
        "title": title,
        "description": description,
        "url": _proxy_aware_external_url(
            url_for(
                "meeting_shares.public_shared_meeting",
                share_id=str((request.view_args or {}).get("share_id") or ""),
                lang=language,
                _external=True,
            )
        ),
        "image_url": _proxy_aware_external_url(image_url),
        "image_alt": image_alt,
    }


def _share_language(share: dict) -> str:
    return (
        supported_language(request.args.get("lang"))
        or supported_language(share.get("language"))
        or supported_language(session.get("language"))
        or "en"
    )


def _proxy_aware_external_url(url: str) -> str:
    forwarded_proto = (
        request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    )
    if forwarded_proto not in {"http", "https"}:
        return url
    if "://" not in url:
        return url
    return f"{forwarded_proto}://{url.split('://', 1)[1]}"


def _is_unlocked(share_id: str) -> bool:
    return share_id in set(session.get("unlocked_meeting_shares", []))


def _check_unlock_rate_limit(share_id: str) -> None:
    limit = 10
    window_seconds = 900
    key = f"{request.remote_addr or 'unknown'}:{share_id}"
    now = time.monotonic()
    with _UNLOCK_LOCK:
        attempts = _UNLOCK_ATTEMPTS[key]
        while attempts and attempts[0] <= now - window_seconds:
            attempts.popleft()
        if len(attempts) >= limit:
            raise RateLimitError("Too many password attempts. Try again later.")
        attempts.append(now)


def _public_response(body: str, status: int = 200):
    response = make_response(body, status)
    return _secure_public_headers(response)


def _public_error(message: str, status: int):
    return _public_response(
        _render_shared_meeting(
            share={},
            snapshot={},
            password_required=False,
            password_error="",
            unavailable_message=message,
        ),
        status=status,
    )


def _secure_public_headers(response):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; "
        "img-src 'self' data:; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response
