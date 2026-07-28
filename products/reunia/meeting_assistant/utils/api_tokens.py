from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask import current_app


_TOKEN_SALT = "meeting-assistant-api-token"


def generate_api_token(user_id: str) -> str:
    serializer = URLSafeTimedSerializer(current_app.secret_key, salt=_TOKEN_SALT)
    return serializer.dumps({"user_id": user_id})


def verify_api_token(token: str) -> str | None:
    serializer = URLSafeTimedSerializer(current_app.secret_key, salt=_TOKEN_SALT)
    try:
        payload = serializer.loads(
            token,
            max_age=current_app.config["API_TOKEN_MAX_AGE_SECONDS"],
        )
    except (BadSignature, SignatureExpired):
        return None
    user_id = payload.get("user_id")
    return str(user_id) if user_id else None
