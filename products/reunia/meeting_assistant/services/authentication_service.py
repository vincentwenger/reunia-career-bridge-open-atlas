from __future__ import annotations

import hashlib
import hmac
import re
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from botocore.exceptions import ClientError
from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from meeting_assistant.i18n import normalize_language
from meeting_assistant.repositories.user_repository import UserRepository
from meeting_assistant.services.user_service import (
    default_assistant_context,
    default_user_settings,
)
from meeting_assistant.utils.exceptions import AuthenticationError, DatabaseError, ValidationError

_EMAIL_RE = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)
_PASSWORD_RESET_SALT = "reunia-password-reset-v1"


def normalize_email(value: str) -> str:
    """Return the canonical account identifier used for new users."""
    return str(value or "").strip().lower()


def validate_email(value: str) -> str:
    email = normalize_email(value)
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise ValidationError("Please enter a valid email address.")
    return email


class AuthenticationService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def authenticate(self, user_id: str, password: str) -> dict:
        raw_user_id = str(user_id or "").strip()
        if not raw_user_id or not password:
            raise ValidationError("user_id and password are required.")

        canonical_user_id = normalize_email(raw_user_id)
        candidates = [canonical_user_id]
        # Preserve sign-in compatibility for accounts created before email
        # identifiers were normalized to lowercase.
        if raw_user_id != canonical_user_id:
            candidates.append(raw_user_id)

        try:
            user = None
            for candidate in candidates:
                user = self.repository.get_by_id(candidate)
                if user:
                    break
        except ClientError as exc:
            raise DatabaseError("A database communication error occurred.") from exc

        if not user or not check_password_hash(user.get("password_hash", ""), password):
            raise AuthenticationError("Invalid user_id or password.")

        return user

    def register(
        self,
        full_name: str,
        email: str,
        password: str,
        language: str | None = None,
    ) -> dict:
        normalized_name = str(full_name or "").strip()
        if not normalized_name or not email or not password:
            raise ValidationError("Full name, email, and password are required.")
        if len(normalized_name) > 120:
            raise ValidationError("Full name must contain 120 characters or fewer.")
        canonical_email = validate_email(email)
        self._validate_password(password)

        settings = default_user_settings()
        settings["language"] = normalize_language(language, default="en")

        user = {
            "user_id": canonical_email,
            "full_name": normalized_name,
            "email": canonical_email,
            "password_hash": generate_password_hash(password),
            "settings": settings,
            "assistant_context": default_assistant_context(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.repository.create(user)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise ValidationError(
                    "This email is already registered. Please log in instead."
                ) from exc
            raise DatabaseError("We encountered a database error.") from exc

        return user

    def create_password_reset_token(self, email: str) -> tuple[dict, str] | None:
        """Create a reset token, returning ``None`` for unknown accounts."""
        raw_email = str(email or "").strip()
        try:
            canonical_email = validate_email(raw_email)
        except ValidationError:
            return None

        candidates = [canonical_email]
        if raw_email != canonical_email:
            candidates.append(raw_email)

        try:
            user = None
            for candidate in candidates:
                user = self.repository.get_by_id(candidate)
                if user:
                    break
        except ClientError as exc:
            raise DatabaseError("A database communication error occurred.") from exc

        if not user:
            return None

        payload = {
            "user_id": user["user_id"],
            "password_fingerprint": self._password_fingerprint(user),
        }
        token = self._password_reset_serializer().dumps(payload)
        return user, token

    def validate_password_reset_token(self, token: str) -> dict:
        try:
            payload = self._password_reset_serializer().loads(
                str(token or ""),
                max_age=int(current_app.config["PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS"]),
            )
        except SignatureExpired as exc:
            raise ValidationError(
                "This password reset link has expired. Request a new one."
            ) from exc
        except BadSignature as exc:
            raise ValidationError(
                "This password reset link is invalid. Request a new one."
            ) from exc

        user_id = str(payload.get("user_id") or "")
        try:
            user = self.repository.get_by_id(user_id) if user_id else None
        except ClientError as exc:
            raise DatabaseError("A database communication error occurred.") from exc

        expected_fingerprint = str(payload.get("password_fingerprint") or "")
        if not user or not hmac.compare_digest(
            expected_fingerprint,
            self._password_fingerprint(user),
        ):
            raise ValidationError(
                "This password reset link is no longer valid. Request a new one."
            )
        return user

    def reset_password(self, token: str, password: str) -> dict:
        user = self.validate_password_reset_token(token)
        self._validate_password(password)
        try:
            self.repository.update_fields(
                user["user_id"],
                {"password_hash": generate_password_hash(password)},
            )
        except ClientError as exc:
            raise DatabaseError("We could not update your password. Please try again.") from exc
        return user

    @staticmethod
    def send_password_reset_email(user: dict, reset_url: str) -> bool:
        host = str(current_app.config.get("SUPPORT_SMTP_HOST") or "").strip()
        sender = str(
            current_app.config.get("SUPPORT_FROM_EMAIL")
            or current_app.config.get("SUPPORT_EMAIL")
            or ""
        ).strip()
        recipient = str(user.get("email") or user.get("user_id") or "").strip()
        if not host or not sender or not recipient:
            current_app.logger.warning(
                "Password reset email was requested, but SMTP is not configured."
            )
            return False

        message = EmailMessage()
        message["Subject"] = "Reset your RÃ©unia password"
        message["From"] = sender
        message["To"] = recipient
        message.set_content(
            "We received a request to reset your RÃ©unia password.\n\n"
            f"Reset your password: {reset_url}\n\n"
            "If you did not request this, you can ignore this email. The link "
            "expires automatically and can only be used while your current "
            "password remains unchanged."
        )

        try:
            use_ssl = bool(current_app.config["SUPPORT_SMTP_USE_SSL"])
            smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_class(
                host,
                current_app.config["SUPPORT_SMTP_PORT"],
                timeout=20,
            ) as smtp:
                if not use_ssl and current_app.config["SUPPORT_SMTP_USE_TLS"]:
                    smtp.starttls()
                username = str(current_app.config.get("SUPPORT_SMTP_USERNAME") or "")
                if username:
                    smtp.login(
                        username,
                        str(current_app.config.get("SUPPORT_SMTP_PASSWORD") or ""),
                    )
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            current_app.logger.exception(
                "Could not send password reset email for %s", user["user_id"]
            )
            return False
        return True

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8:
            raise ValidationError("Password must contain at least 8 characters.")
        if len(password) > 256:
            raise ValidationError("Password must contain 256 characters or fewer.")

    @staticmethod
    def _password_fingerprint(user: dict) -> str:
        return hashlib.sha256(
            str(user.get("password_hash") or "").encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _password_reset_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"],
            salt=_PASSWORD_RESET_SALT,
        )
