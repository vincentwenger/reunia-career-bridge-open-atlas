from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import PurePath
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from meeting_assistant.utils.exceptions import DatabaseError, RateLimitError, ValidationError


_ALLOWED_TOPICS = {
    "question": "How-to question",
    "technical": "Technical problem",
    "account": "Account or sign-in",
    "billing": "Billing",
    "privacy": "Privacy or data handling",
    "feedback": "General feedback",
    "feature": "Feature suggestion",
    "other": "Other",
    # Legacy values remain accepted so older pages or clients do not break.
    "using-app": "Using Réunia",
    "recording": "Browser recording",
    "transcript": "Transcript or Meeting Review",
    "knowledge": "Meeting Preparation / Knowledge",
    "bug": "Report a bug",
}

_ALLOWED_AREAS = {
    "getting-started": "Home page or getting started",
    "preparation": "Meeting Preparation",
    "recorder": "Browser Meeting Recorder",
    "desktop-recorder": "Windows Desktop Recorder",
    "meeting-review": "Meeting Review",
    "sharing": "Meeting sharing",
    "action-center": "Career Action Plan",
    "analytics": "Analytics",
    "settings": "Settings",
    "account": "Profile or account",
    "other": "Other",
}

_ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "txt", "log"}
_ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "application/pdf",
    "text/plain",
    "application/octet-stream",
}


class SupportService:
    def __init__(self, repository=None, rate_limiter=None) -> None:
        self.repository = repository or current_app.extensions["support_repository"]
        self.rate_limiter = rate_limiter or current_app.extensions["support_rate_limiter"]

    def submit(
        self,
        *,
        form: Any,
        attachment: FileStorage | None,
        user_id: str | None,
        remote_address: str,
        user_agent: str,
        page_url: str,
        source: str = "web_support_form",
        message_maximum: int = 5000,
    ) -> dict[str, Any]:
        self._check_rate_limit(user_id=user_id, remote_address=remote_address)

        # Honeypot fields should remain empty. Never return a success response for
        # a request that was not stored, because browser autofill can occasionally
        # populate hidden fields and otherwise create a misleading confirmation.
        if str(form.get("website") or "").strip():
            current_app.logger.warning(
                "Rejected support submission because the spam-check field was populated."
            )
            raise ValidationError(
                "We could not verify the submission. Refresh the page and try again."
            )

        cleaned = self._validate_fields(form, message_maximum=message_maximum)
        request_id = self._new_request_id()
        created_at = datetime.now(timezone.utc).isoformat()
        attachment_data = self._prepare_attachment(attachment, request_id, user_id)

        item: dict[str, Any] = {
            "request_id": request_id,
            "created_at": created_at,
            "status": "new",
            "name": cleaned["name"],
            "email": cleaned["email"],
            "topic": cleaned["topic"],
            "topic_label": _ALLOWED_TOPICS[cleaned["topic"]],
            "area": cleaned["area"],
            "area_label": _ALLOWED_AREAS[cleaned["area"]],
            "subject": cleaned["subject"],
            "message": cleaned["message"],
            "source": self._clean_optional(source, 80) or "web_support_form",
            "page_url": self._clean_optional(page_url, 1000),
            "user_agent": self._clean_optional(user_agent, 1000),
            "remote_address": self._clean_optional(remote_address, 128),
        }
        if user_id:
            item["user_id"] = str(user_id)
        if attachment_data:
            item["attachment"] = attachment_data["metadata"]

        try:
            self.repository.create(item)
        except (BotoCoreError, ClientError) as exc:
            self._delete_uploaded_attachment(attachment_data)
            current_app.logger.exception("Could not store support request %s", request_id)
            raise DatabaseError("We could not save your support request. Please try again.") from exc

        self._send_optional_email(item, attachment_data)

        current_app.logger.info(
            "Support request %s created for topic %s",
            request_id,
            cleaned["topic"],
        )
        return {
            "request_id": request_id,
            "stored": True,
            "message": self._success_message(request_id),
        }

    def submit_recorder_error(
        self,
        *,
        payload: Any,
        user_id: str,
        user_name: str,
        user_email: str,
        remote_address: str,
        user_agent: str,
        page_url: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("The recorder error details are invalid.")

        diagnostics = self._required_text(
            payload.get("diagnostic_details"),
            "Diagnostic details",
            12000,
        )
        reference_id = self._clean_optional(payload.get("reference_id"), 160) or "Unavailable"
        error_message = self._clean_optional(payload.get("error_message"), 1200) or "Unavailable"
        stage = self._clean_optional(payload.get("stage"), 120) or "Unknown"
        http_status = self._clean_optional(payload.get("http_status"), 80) or "Unavailable"
        status_text = self._clean_optional(payload.get("status_text"), 120)
        http_summary = f"{http_status} {status_text}".strip()
        recording = self._clean_optional(payload.get("recording"), 500) or "Unavailable"
        occurred_at = self._clean_optional(payload.get("occurred_at"), 80) or "Unavailable"

        email = self._clean_optional(user_email or user_id, 254).lower()
        if not self._looks_like_email(email):
            raise ValidationError(
                "Your account email is unavailable. Sign out and sign in again, then resend the error."
            )
        name = self._clean_optional(user_name, 120) or email.split("@", 1)[0] or "Réunia user"

        subject = f"Browser Recorder error · {reference_id}"[:160]
        message = "\n".join(
            [
                "This support request was sent automatically from the Browser Meeting Recorder.",
                f"Recorder reference: {reference_id}",
                f"Error message: {error_message}",
                f"Failed stage: {stage}",
                f"HTTP status: {http_summary}",
                f"Recording: {recording}",
                f"Reported at: {occurred_at}",
                "",
                "Diagnostic details:",
                diagnostics,
            ]
        )

        return self.submit(
            form={
                "name": name,
                "email": email,
                "topic": "technical",
                "area": "recorder",
                "subject": subject,
                "message": message,
            },
            attachment=None,
            user_id=user_id,
            remote_address=remote_address,
            user_agent=user_agent,
            page_url=page_url,
            source="browser_recorder_error",
            message_maximum=16000,
        )

    def _check_rate_limit(self, *, user_id: str | None, remote_address: str) -> None:
        key = f"user:{user_id}" if user_id else f"ip:{remote_address or 'unknown'}"
        allowed, _retry_after = self.rate_limiter.hit(
            f"support:{key}",
            limit=current_app.config["SUPPORT_RATE_LIMIT_COUNT"],
            window_seconds=current_app.config["SUPPORT_RATE_LIMIT_WINDOW_SECONDS"],
        )
        if not allowed:
            raise RateLimitError(
                "Too many support requests were sent recently. Please wait and try again."
            )

    def _validate_fields(
        self,
        form: Any,
        *,
        message_maximum: int = 5000,
    ) -> dict[str, str]:
        name = self._required_text(form.get("name"), "Name", 120)
        email = self._required_text(form.get("email"), "Email address", 254).lower()
        topic = self._required_text(form.get("topic"), "Request type", 50)
        area = self._clean_optional(form.get("area"), 50) or "other"
        subject = self._required_text(form.get("subject"), "Subject", 160)
        message = self._required_text(form.get("message"), "Message", message_maximum)

        if not self._looks_like_email(email):
            raise ValidationError("Enter a valid email address.")
        if topic not in _ALLOWED_TOPICS:
            raise ValidationError("Select a valid request type.")
        if area not in _ALLOWED_AREAS:
            raise ValidationError("Select a valid feature or area.")

        return {
            "name": name,
            "email": email,
            "topic": topic,
            "area": area,
            "subject": subject,
            "message": message,
        }

    def _prepare_attachment(
        self,
        attachment: FileStorage | None,
        request_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        if attachment is None or not attachment.filename:
            return None

        filename = secure_filename(PurePath(attachment.filename).name)
        if not filename or "." not in filename:
            raise ValidationError("The attachment must have a supported file extension.")

        extension = filename.rsplit(".", 1)[1].lower()
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValidationError("Choose a PNG, JPG, PDF, TXT, or LOG file.")

        maximum_size = current_app.config["SUPPORT_MAX_ATTACHMENT_BYTES"]
        content = attachment.stream.read(maximum_size + 1)
        if len(content) > maximum_size:
            raise ValidationError("The attachment must be 5 MB or smaller.")
        if not content:
            raise ValidationError("The selected attachment is empty.")

        detected_type = self._validate_file_content(extension, content)
        supplied_type = (attachment.mimetype or "").lower()
        if supplied_type and supplied_type not in _ALLOWED_MIME_TYPES:
            raise ValidationError("The attachment type is not supported.")

        bucket = str(current_app.config.get("SUPPORT_ATTACHMENTS_BUCKET") or "").strip()
        if not bucket:
            raise ValidationError(
                "File attachments are not enabled yet. Remove the file and send the request again."
            )

        owner = secure_filename(str(user_id or "anonymous")) or "anonymous"
        object_key = f"support/{owner}/{request_id}/{filename}"
        extra_args: dict[str, Any] = {
            "Bucket": bucket,
            "Key": object_key,
            "Body": content,
            "ContentType": detected_type,
            "ServerSideEncryption": "AES256",
            "Metadata": {"support-request-id": request_id},
        }

        try:
            boto3.client("s3", region_name=current_app.config["AWS_REGION"]).put_object(
                **extra_args
            )
        except (BotoCoreError, ClientError) as exc:
            current_app.logger.exception("Could not upload support attachment for %s", request_id)
            raise DatabaseError(
                "We could not save the attachment. Remove it or try again later."
            ) from exc

        return {
            "bytes": content,
            "metadata": {
                "filename": filename,
                "content_type": detected_type,
                "size_bytes": len(content),
                "bucket": bucket,
                "object_key": object_key,
            },
        }


    def _delete_uploaded_attachment(self, attachment_data: dict[str, Any] | None) -> None:
        if not attachment_data:
            return
        metadata = attachment_data.get("metadata", {})
        bucket = metadata.get("bucket")
        object_key = metadata.get("object_key")
        if not bucket or not object_key:
            return
        try:
            boto3.client("s3", region_name=current_app.config["AWS_REGION"]).delete_object(
                Bucket=bucket,
                Key=object_key,
            )
        except (BotoCoreError, ClientError):
            current_app.logger.exception(
                "Could not remove orphaned support attachment %s", object_key
            )

    def _send_optional_email(
        self,
        item: dict[str, Any],
        attachment_data: dict[str, Any] | None,
    ) -> None:
        host = str(current_app.config.get("SUPPORT_SMTP_HOST") or "").strip()
        recipient = str(current_app.config.get("SUPPORT_EMAIL") or "").strip()
        sender = str(current_app.config.get("SUPPORT_FROM_EMAIL") or recipient).strip()
        if not host or not recipient or not sender:
            return

        message = EmailMessage()
        message["Subject"] = f"[Réunia Support] {item['subject']}"
        message["From"] = sender
        message["To"] = recipient
        message["Reply-To"] = item["email"]
        message.set_content(self._email_body(item))

        if attachment_data:
            metadata = attachment_data["metadata"]
            maintype, subtype = metadata["content_type"].split("/", 1)
            message.add_attachment(
                attachment_data["bytes"],
                maintype=maintype,
                subtype=subtype,
                filename=metadata["filename"],
            )

        try:
            use_ssl = current_app.config["SUPPORT_SMTP_USE_SSL"]
            smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_class(
                host,
                current_app.config["SUPPORT_SMTP_PORT"],
                timeout=20,
            ) as smtp:
                if not use_ssl and current_app.config["SUPPORT_SMTP_USE_TLS"]:
                    smtp.starttls()
                username = str(current_app.config.get("SUPPORT_SMTP_USERNAME") or "")
                password = str(current_app.config.get("SUPPORT_SMTP_PASSWORD") or "")
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            # The request has already been stored. Do not make the user resubmit it.
            current_app.logger.exception(
                "Support request %s was stored, but its notification email failed.",
                item["request_id"],
            )

    @staticmethod
    def _email_body(item: dict[str, Any]) -> str:
        lines = [
            f"Request ID: {item['request_id']}",
            f"Created: {item['created_at']}",
            f"Name: {item['name']}",
            f"Email: {item['email']}",
            f"Request type: {item['topic_label']}",
            f"Feature or area: {item.get('area_label', 'Other')}",
            f"User ID: {item.get('user_id', 'Not signed in')}",
            f"Page: {item.get('page_url', '')}",
            "",
            item["message"],
        ]
        return "\n".join(lines)

    @staticmethod
    def _validate_file_content(extension: str, content: bytes) -> str:
        if extension == "png":
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValidationError("The selected PNG file is not valid.")
            return "image/png"
        if extension in {"jpg", "jpeg"}:
            if not (content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")):
                raise ValidationError("The selected JPG file is not valid.")
            return "image/jpeg"
        if extension == "pdf":
            if not content.startswith(b"%PDF-"):
                raise ValidationError("The selected PDF file is not valid.")
            return "application/pdf"

        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("TXT and LOG attachments must contain readable text.") from exc
        return "text/plain"

    @staticmethod
    def _required_text(value: Any, label: str, maximum: int) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValidationError(f"{label} is required.")
        if len(cleaned) > maximum:
            raise ValidationError(f"{label} must be {maximum} characters or fewer.")
        return cleaned

    @staticmethod
    def _clean_optional(value: Any, maximum: int) -> str:
        return str(value or "").strip()[:maximum]

    @staticmethod
    def _looks_like_email(value: str) -> bool:
        if len(value) > 254 or value.count("@") != 1:
            return False
        local, domain = value.rsplit("@", 1)
        return bool(local and "." in domain and not domain.startswith(".") and not domain.endswith("."))

    @staticmethod
    def _success_message(request_id: str) -> str:
        base = str(current_app.config["SUPPORT_SUCCESS_MESSAGE"]).strip().rstrip(".")
        return f"{base}. Reference: {request_id}."

    @staticmethod
    def _new_request_id() -> str:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"SUP-{date}-{uuid4().hex[:8].upper()}"
