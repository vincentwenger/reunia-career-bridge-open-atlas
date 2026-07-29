"""Centralized application configuration."""
from __future__ import annotations

import json
import os


def _pricing_table_from_env(
    environment_variable: str,
    defaults: dict[str, dict[str, float] | float],
) -> dict[str, dict[str, float] | float]:
    """Return a validated pricing table with optional deployment overrides.

    The defaults keep analytics useful without requiring extra environment
    variables. Deployments can replace or extend individual model entries with
    a JSON object when OpenAI pricing changes.
    """
    table = dict(defaults)
    raw_value = os.getenv(environment_variable, "").strip()
    if not raw_value:
        return table
    try:
        overrides = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return table
    if not isinstance(overrides, dict):
        return table

    for model, pricing in overrides.items():
        normalized_model = str(model or "").strip().lower()
        if not normalized_model:
            continue
        if isinstance(pricing, (int, float)):
            table[normalized_model] = max(0.0, float(pricing))
            continue
        if not isinstance(pricing, dict):
            continue
        safe_pricing: dict[str, float] = {}
        for field in ("input", "cached_input", "output"):
            try:
                if pricing.get(field) is not None:
                    safe_pricing[field] = max(0.0, float(pricing[field]))
            except (TypeError, ValueError):
                continue
        if safe_pricing:
            table[normalized_model] = safe_pricing
    return table


class BaseConfig:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or "development-only-change-me"

    AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))

    # User-facing AI model presets. The Settings page displays simple capability
    # choices while the backend maps each choice to a deployment-configured model ID.
    AI_MODEL_FAST = os.getenv("AI_MODEL_FAST", "gpt-4o-mini").strip()
    AI_MODEL_BALANCED = os.getenv("AI_MODEL_BALANCED", "gpt-5.4-mini").strip()
    AI_MODEL_ADVANCED = os.getenv("AI_MODEL_ADVANCED", "gpt-5.4").strip()
    AI_MODEL_PRESETS = {
        "fast": AI_MODEL_FAST,
        "balanced": AI_MODEL_BALANCED,
        "advanced": AI_MODEL_ADVANCED,
    }
    AI_MAX_MODEL_PRESET = os.getenv("AI_MAX_MODEL_PRESET", "fast").strip().lower()
    if AI_MAX_MODEL_PRESET not in AI_MODEL_PRESETS:
        AI_MAX_MODEL_PRESET = "fast"
    DEFAULT_AI_MODEL_PRESET = os.getenv(
        "DEFAULT_AI_MODEL_PRESET",
        "fast",
    ).strip().lower()
    if DEFAULT_AI_MODEL_PRESET not in AI_MODEL_PRESETS:
        DEFAULT_AI_MODEL_PRESET = "fast"

    DEFAULT_AI_MODEL = os.getenv(
        "DEFAULT_AI_MODEL",
        os.getenv(
            "default_model",
            AI_MODEL_PRESETS[DEFAULT_AI_MODEL_PRESET],
        ),
    ).strip()
    # Final recordings keep Whisper's timestamped segments. Short Live Q&A
    # windows use the lower-cost transcription model and do not request timestamps.
    FINAL_TRANSCRIPTION_MODEL = os.getenv(
        "FINAL_TRANSCRIPTION_MODEL",
        os.getenv("AUDIO_TRANSCRIPTION_MODEL", "whisper-1"),
    ).strip()
    LIVE_TRANSCRIPTION_MODEL = os.getenv(
        "LIVE_TRANSCRIPTION_MODEL",
        "gpt-4o-mini-transcribe",
    ).strip()
    # Backward-compatible alias used by older integrations.
    AUDIO_TRANSCRIPTION_MODEL = FINAL_TRANSCRIPTION_MODEL
    AUDIO_TRANSCRIPTION_LANGUAGE = os.getenv(
        "AUDIO_TRANSCRIPTION_LANGUAGE",
        "en",
    ).strip()
    RECORDER_NO_SPEECH_PROBABILITY_THRESHOLD = float(
        os.getenv("RECORDER_NO_SPEECH_PROBABILITY_THRESHOLD", "0.60")
    )
    RECORDER_HIGH_NO_SPEECH_PROBABILITY_THRESHOLD = float(
        os.getenv("RECORDER_HIGH_NO_SPEECH_PROBABILITY_THRESHOLD", "0.80")
    )
    RECORDER_MIN_AVG_LOGPROB = float(
        os.getenv("RECORDER_MIN_AVG_LOGPROB", "-1.0")
    )
    RECORDER_VERY_LOW_AVG_LOGPROB = float(
        os.getenv("RECORDER_VERY_LOW_AVG_LOGPROB", "-1.5")
    )
    RECORDER_MAX_COMPRESSION_RATIO = float(
        os.getenv("RECORDER_MAX_COMPRESSION_RATIO", "2.4")
    )
    RECORDER_REPEAT_MIN_WORDS = int(
        os.getenv("RECORDER_REPEAT_MIN_WORDS", "5")
    )
    RECORDER_REPEAT_ALLOW_COUNT = int(
        os.getenv("RECORDER_REPEAT_ALLOW_COUNT", "1")
    )
    RECORDER_REPEAT_TRIGGER_COUNT = int(
        os.getenv("RECORDER_REPEAT_TRIGGER_COUNT", "3")
    )
    RECORDER_REPEAT_WINDOW_SECONDS = float(
        os.getenv("RECORDER_REPEAT_WINDOW_SECONDS", "180")
    )
    # Each final browser recording segment must remain below both the application
    # upload ceiling and the transcription provider's per-request file limit.
    RECORDER_MAX_FILE_BYTES = int(
        os.getenv("RECORDER_MAX_FILE_BYTES", str(24_000_000))
    )
    RECORDER_FINAL_SEGMENT_SECONDS = int(
        os.getenv("RECORDER_FINAL_SEGMENT_SECONDS", "600")
    )
    RECORDER_FINAL_SEGMENT_RETRY_COUNT = int(
        os.getenv("RECORDER_FINAL_SEGMENT_RETRY_COUNT", "3")
    )
    RECORDER_FINAL_SEGMENT_RETRY_BASE_MILLISECONDS = int(
        os.getenv("RECORDER_FINAL_SEGMENT_RETRY_BASE_MILLISECONDS", "1000")
    )
    RECORDER_FINAL_MIN_SEGMENT_BYTES = int(
        os.getenv("RECORDER_FINAL_MIN_SEGMENT_BYTES", "800")
    )
    RECORDER_MAX_SEGMENTS_PER_SOURCE = int(
        os.getenv("RECORDER_MAX_SEGMENTS_PER_SOURCE", "18")
    )
    RECORDER_MAX_TOTAL_BYTES = int(
        os.getenv("RECORDER_MAX_TOTAL_BYTES", str(750_000_000))
    )
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(60_000_000)))
    RECORDER_JOB_DIR = os.getenv(
        "RECORDER_JOB_DIR",
        "/tmp/meeting-assistant-recorder-jobs",
    )
    RECORDER_JOB_RETENTION_SECONDS = int(
        os.getenv("RECORDER_JOB_RETENTION_SECONDS", "86400")
    )
    RECORDER_JOB_IDEMPOTENCY_CHECK = os.getenv(
        "RECORDER_JOB_IDEMPOTENCY_CHECK", "true"
    ).strip().lower() == "true"
    RECORDER_JOB_STORAGE_BACKEND = os.getenv(
        "RECORDER_JOB_STORAGE_BACKEND", "local"
    ).strip().lower()
    RECORDER_JOB_QUEUE_BACKEND = os.getenv(
        "RECORDER_JOB_QUEUE_BACKEND", "inline"
    ).strip().lower()
    RECORDER_LIVE_STATE_BACKEND = os.getenv(
        "RECORDER_LIVE_STATE_BACKEND", "memory"
    ).strip().lower()
    RECORDER_JOBS_BUCKET = os.getenv("RECORDER_JOBS_BUCKET", "").strip()
    RECORDER_JOBS_S3_PREFIX = os.getenv(
        "RECORDER_JOBS_S3_PREFIX", "recorder-jobs"
    ).strip().strip("/")
    RECORDER_JOB_LEASE_SECONDS = int(
        os.getenv("RECORDER_JOB_LEASE_SECONDS", "120")
    )
    RECORDER_S3_ACCESS_KEY_ID = os.getenv(
        "RECORDER_S3_ACCESS_KEY_ID",
        os.getenv("KNOWLEDGE_S3_ACCESS_KEY_ID", ""),
    ).strip()
    RECORDER_S3_SECRET_ACCESS_KEY = os.getenv(
        "RECORDER_S3_SECRET_ACCESS_KEY",
        os.getenv("KNOWLEDGE_S3_SECRET_ACCESS_KEY", ""),
    ).strip()
    RECORDER_S3_SESSION_TOKEN = os.getenv(
        "RECORDER_S3_SESSION_TOKEN",
        os.getenv("KNOWLEDGE_S3_SESSION_TOKEN", ""),
    ).strip()

    # Browser Recorder live-audio windows. A 10-second window starts every
    # 9 seconds, preserving a small boundary overlap without rebilling 25% of audio.
    RECORDER_LIVE_CHUNK_WINDOW_SECONDS = float(
        os.getenv("RECORDER_LIVE_CHUNK_WINDOW_SECONDS", "10")
    )
    RECORDER_LIVE_CHUNK_INTERVAL_SECONDS = float(
        os.getenv("RECORDER_LIVE_CHUNK_INTERVAL_SECONDS", "9")
    )
    RECORDER_LIVE_QUEUE_LIMIT = int(
        os.getenv("RECORDER_LIVE_QUEUE_LIMIT", "3")
    )
    RECORDER_LIVE_RETRY_COUNT = int(
        os.getenv("RECORDER_LIVE_RETRY_COUNT", "2")
    )
    RECORDER_LIVE_RETRY_BASE_MILLISECONDS = int(
        os.getenv("RECORDER_LIVE_RETRY_BASE_MILLISECONDS", "600")
    )
    RECORDER_LIVE_MIN_CHUNK_BYTES = int(
        os.getenv("RECORDER_LIVE_MIN_CHUNK_BYTES", "800")
    )
    RECORDER_LIVE_OVERLAP_MIN_WORDS = int(
        os.getenv("RECORDER_LIVE_OVERLAP_MIN_WORDS", "3")
    )


    # Cost controls. Values of 0 disable the corresponding limit. Defaults are
    # intentionally conservative and can be raised per deployment.
    AI_ENABLED = os.getenv("AI_ENABLED", "true").strip().lower() == "true"
    AI_GLOBAL_DAILY_BUDGET_USD = float(os.getenv("AI_GLOBAL_DAILY_BUDGET_USD", "10"))
    AI_GLOBAL_MONTHLY_BUDGET_USD = float(os.getenv("AI_GLOBAL_MONTHLY_BUDGET_USD", "100"))
    AI_USER_DAILY_BUDGET_USD = float(os.getenv("AI_USER_DAILY_BUDGET_USD", "2"))
    AI_USER_DAILY_TRANSCRIPTION_MINUTES = float(
        os.getenv("AI_USER_DAILY_TRANSCRIPTION_MINUTES", "180")
    )
    AI_LIVE_QA_MAX_MINUTES_PER_MEETING = float(
        os.getenv("AI_LIVE_QA_MAX_MINUTES_PER_MEETING", "60")
    )
    AI_LIVE_QA_MAX_ANSWERS_PER_MEETING = int(
        os.getenv("AI_LIVE_QA_MAX_ANSWERS_PER_MEETING", "25")
    )
    AI_UNPRICED_TEXT_REQUEST_RESERVE_USD = float(
        os.getenv("AI_UNPRICED_TEXT_REQUEST_RESERVE_USD", "0.05")
    )
    AI_UNPRICED_TRANSCRIPTION_RESERVE_PER_MINUTE_USD = float(
        os.getenv("AI_UNPRICED_TRANSCRIPTION_RESERVE_PER_MINUTE_USD", "0.02")
    )
    AI_MAX_OUTPUT_TOKENS_LIVE_QA = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_LIVE_QA", "400")
    )
    AI_MAX_OUTPUT_TOKENS_KNOWLEDGE_SEARCH = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_KNOWLEDGE_SEARCH", "700")
    )
    AI_MAX_OUTPUT_TOKENS_MEETING_ANALYSIS = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_MEETING_ANALYSIS", "2600")
    )
    AI_COMBINE_MEETING_ANALYSIS_REQUESTS = (
        os.getenv("AI_COMBINE_MEETING_ANALYSIS_REQUESTS", "true").strip().lower() == "true"
    )
    AI_RESPONSE_CACHE_SECONDS = int(os.getenv("AI_RESPONSE_CACHE_SECONDS", "3600"))
    RECORDER_LIVE_SPEECH_LEVEL_THRESHOLD = float(
        os.getenv("RECORDER_LIVE_SPEECH_LEVEL_THRESHOLD", "8")
    )
    RECORDER_LIVE_MIN_SPEECH_RATIO = float(
        os.getenv("RECORDER_LIVE_MIN_SPEECH_RATIO", "0.08")
    )

    USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "").strip()
    TRANSCRIPTS_TABLE_NAME = os.getenv("TRANSCRIPTS_TABLE_NAME", "").strip()
    TRANSCRIPTS_USER_INDEX = os.getenv("TRANSCRIPTS_USER_INDEX", "user_id-index")

    ACTIONS_TABLE_NAME = os.getenv("ACTIONS_TABLE_NAME", "").strip()

    # Administrator-only product analytics. ADMIN_USER_IDS is a comma-separated
    # allowlist of account email addresses/user IDs. Existing user records may
    # also set is_admin=true; that value is loaded into the session at login.
    ADMIN_USER_IDS = tuple(
        value.strip().lower()
        for value in os.getenv("ADMIN_USER_IDS", "").split(",")
        if value.strip()
    )

    # Live Interview Assistance is restored behind server-side access control.
    # Administrators always have access. Approved groups and users can be
    # configured here, while per-user overrides are stored in the user record.
    LIVE_INTERVIEW_ASSISTANCE_GROUPS = tuple(
        value.strip().lower()
        for value in os.getenv(
            "LIVE_INTERVIEW_ASSISTANCE_GROUPS",
            "career_bridge_beta,career_coaches",
        ).split(",")
        if value.strip()
    )
    LIVE_INTERVIEW_ASSISTANCE_USER_IDS = tuple(
        value.strip().lower()
        for value in os.getenv("LIVE_INTERVIEW_ASSISTANCE_USER_IDS", "").split(",")
        if value.strip()
    )
    ANALYTICS_TABLE_NAME = os.getenv("ANALYTICS_TABLE_NAME", "").strip()
    ANALYTICS_STORAGE_BACKEND = os.getenv(
        "ANALYTICS_STORAGE_BACKEND",
        "memory",
    ).strip().lower()
    ANALYTICS_EXCLUDE_ADMIN_ACTIVITY = os.getenv(
        "ANALYTICS_EXCLUDE_ADMIN_ACTIVITY",
        "true",
    ).strip().lower() == "true"
    ANALYTICS_HEARTBEAT_SECONDS = int(
        os.getenv("ANALYTICS_HEARTBEAT_SECONDS", "30")
    )
    ANALYTICS_MAX_HEARTBEAT_SECONDS = int(
        os.getenv("ANALYTICS_MAX_HEARTBEAT_SECONDS", "60")
    )
    ANALYTICS_IGNORE_BOTS = os.getenv(
        "ANALYTICS_IGNORE_BOTS", "true"
    ).strip().lower() == "true"
    # Optional two-letter country code supplied and overwritten by a trusted
    # reverse proxy (for example CloudFront-Viewer-Country or CF-IPCountry).
    # Leave blank when requests can reach the app without that trusted proxy.
    ANALYTICS_GEO_COUNTRY_HEADER = os.getenv(
        "ANALYTICS_GEO_COUNTRY_HEADER", ""
    ).strip()
    ANALYTICS_DATE_INDEX = os.getenv(
        "ANALYTICS_DATE_INDEX", "analytics_date-index"
    ).strip()
    ADMIN_ANALYTICS_CACHE_BACKEND = os.getenv(
        "ADMIN_ANALYTICS_CACHE_BACKEND", "memory"
    ).strip().lower()
    ADMIN_ANALYTICS_CACHE_SECONDS = int(
        os.getenv("ADMIN_ANALYTICS_CACHE_SECONDS", "60")
    )
    ANALYTICS_AI_INPUT_COST_PER_MILLION = float(
        os.getenv("ANALYTICS_AI_INPUT_COST_PER_MILLION", "0")
    )
    ANALYTICS_AI_OUTPUT_COST_PER_MILLION = float(
        os.getenv("ANALYTICS_AI_OUTPUT_COST_PER_MILLION", "0")
    )
    # Standard API prices in USD. Text values are per 1 million tokens;
    # transcription values are per audio minute. The tables are intentionally
    # overridable so production can update pricing without a code deployment.
    ANALYTICS_AI_MODEL_PRICING = _pricing_table_from_env(
        "ANALYTICS_AI_PRICING_JSON",
        {
            "gpt-4o-mini": {
                "input": 0.15,
                "cached_input": 0.075,
                "output": 0.60,
            },
            "gpt-5.4-mini": {
                "input": 0.75,
                "cached_input": 0.075,
                "output": 4.50,
            },
            "gpt-5.4": {
                "input": 2.50,
                "cached_input": 0.25,
                "output": 15.00,
            },
        },
    )
    ANALYTICS_TRANSCRIPTION_MODEL_PRICING = _pricing_table_from_env(
        "ANALYTICS_TRANSCRIPTION_PRICING_JSON",
        {
            "whisper-1": 0.006,
            "gpt-4o-transcribe": 0.006,
            "gpt-4o-mini-transcribe": 0.003,
        },
    )
    ACTIONS_STORAGE_BACKEND = os.getenv(
        "ACTIONS_STORAGE_BACKEND",
        "dynamodb",
    ).strip().lower()

    MEETING_SHARES_TABLE_NAME = os.getenv(
        "MEETING_SHARES_TABLE_NAME", ""
    ).strip()
    MEETING_SHARES_STORAGE_BACKEND = os.getenv(
        "MEETING_SHARES_STORAGE_BACKEND",
        "dynamodb",
    ).strip().lower()
    MEETING_SHARES_LOCAL_PATH = os.getenv(
        "MEETING_SHARES_LOCAL_PATH",
        "instance/meeting_shares.json",
    )

    LIVE_QA_TABLE_NAME = os.getenv("LIVE_QA_TABLE_NAME", "").strip()
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    LIVE_QA_STORAGE_BACKEND = os.getenv(
        "LIVE_QA_STORAGE_BACKEND",
        "dynamodb",
    ).strip().lower()
    # Backward-compatible base interval. New deployments should use the active
    # and idle interval settings below.
    LIVE_QA_STREAM_INTERVAL_SECONDS = float(
        os.getenv("LIVE_QA_STREAM_INTERVAL_SECONDS", "2.0")
    )
    LIVE_QA_STREAM_ACTIVE_INTERVAL_SECONDS = float(
        os.getenv(
            "LIVE_QA_STREAM_ACTIVE_INTERVAL_SECONDS",
            str(LIVE_QA_STREAM_INTERVAL_SECONDS),
        )
    )
    LIVE_QA_STREAM_IDLE_INTERVAL_SECONDS = float(
        os.getenv("LIVE_QA_STREAM_IDLE_INTERVAL_SECONDS", "10.0")
    )
    LIVE_QA_STREAM_ACTIVE_WINDOW_SECONDS = float(
        os.getenv("LIVE_QA_STREAM_ACTIVE_WINDOW_SECONDS", "8.0")
    )
    LIVE_QA_STREAM_HEARTBEAT_SECONDS = float(
        os.getenv("LIVE_QA_STREAM_HEARTBEAT_SECONDS", "15.0")
    )
    LIVE_QA_PERSIST_INTERVAL_SECONDS = float(
        os.getenv("LIVE_QA_PERSIST_INTERVAL_SECONDS", "2.0")
    )
    LIVE_QA_DYNAMO_CACHE_TTL_SECONDS = float(
        os.getenv("LIVE_QA_DYNAMO_CACHE_TTL_SECONDS", "2.0")
    )

    API_TOKEN_MAX_AGE_SECONDS = int(os.getenv("API_TOKEN_MAX_AGE_SECONDS", "86400"))
    ALLOW_PASSWORD_AUTH_IN_API_BODY = (
        os.getenv("ALLOW_PASSWORD_AUTH_IN_API_BODY", "false").lower() == "true"
    )
    ALLOW_CLIENT_AI_MODEL_OVERRIDE = (
        os.getenv("ALLOW_CLIENT_AI_MODEL_OVERRIDE", "false").lower() == "true"
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    PERMANENT_SESSION_LIFETIME = 12 * 60 * 60
    PREFERRED_URL_SCHEME = "https"
    TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
    CSRF_ENABLED = os.getenv("CSRF_ENABLED", "true").lower() == "true"
    RATE_LIMIT_STORAGE_BACKEND = os.getenv(
        "RATE_LIMIT_STORAGE_BACKEND", "memory"
    ).strip().lower()
    AUTH_LOGIN_RATE_LIMIT_COUNT = int(
        os.getenv("AUTH_LOGIN_RATE_LIMIT_COUNT", "10")
    )
    AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(
        os.getenv("AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")
    )
    AUTH_SIGNUP_RATE_LIMIT_COUNT = int(
        os.getenv("AUTH_SIGNUP_RATE_LIMIT_COUNT", "5")
    )
    AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS = int(
        os.getenv("AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )
    PASSWORD_RESET_RATE_LIMIT_COUNT = int(
        os.getenv("PASSWORD_RESET_RATE_LIMIT_COUNT", "5")
    )
    PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS = int(
        os.getenv("PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )
    PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS = int(
        os.getenv("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS", "3600")
    )
    ANALYTICS_RATE_LIMIT_COUNT = int(
        os.getenv("ANALYTICS_RATE_LIMIT_COUNT", "180")
    )
    ANALYTICS_RATE_LIMIT_WINDOW_SECONDS = int(
        os.getenv("ANALYTICS_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )

    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
    SUPPORT_FROM_EMAIL = os.getenv("SUPPORT_FROM_EMAIL", SUPPORT_EMAIL)
    SUPPORT_RESPONSE_MESSAGE = os.getenv(
        "SUPPORT_RESPONSE_MESSAGE",
        "We will review your message and reply as soon as possible.",
    )
    SUPPORT_SUCCESS_MESSAGE = os.getenv(
        "SUPPORT_SUCCESS_MESSAGE",
        "Your support request was sent successfully.",
    )
    SUPPORT_REQUESTS_TABLE_NAME = os.getenv(
        "SUPPORT_REQUESTS_TABLE_NAME", ""
    ).strip()
    SUPPORT_STORAGE_BACKEND = os.getenv(
        "SUPPORT_STORAGE_BACKEND",
        "dynamodb",
    ).strip().lower()
    SUPPORT_ATTACHMENTS_BUCKET = os.getenv("SUPPORT_ATTACHMENTS_BUCKET", "")
    SUPPORT_MAX_ATTACHMENT_BYTES = int(
        os.getenv("SUPPORT_MAX_ATTACHMENT_BYTES", str(5 * 1024 * 1024))
    )
    SUPPORT_RATE_LIMIT_COUNT = int(os.getenv("SUPPORT_RATE_LIMIT_COUNT", "5"))
    SUPPORT_RATE_LIMIT_WINDOW_SECONDS = int(
        os.getenv("SUPPORT_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )

    SUPPORT_SMTP_HOST = os.getenv("SUPPORT_SMTP_HOST", "")
    SUPPORT_SMTP_PORT = int(os.getenv("SUPPORT_SMTP_PORT", "587"))
    SUPPORT_SMTP_USERNAME = os.getenv("SUPPORT_SMTP_USERNAME", "")
    SUPPORT_SMTP_PASSWORD = os.getenv("SUPPORT_SMTP_PASSWORD", "")
    SUPPORT_SMTP_USE_TLS = os.getenv("SUPPORT_SMTP_USE_TLS", "true").lower() == "true"
    SUPPORT_SMTP_USE_SSL = os.getenv("SUPPORT_SMTP_USE_SSL", "false").lower() == "true"

    # Document Library metadata and file storage. Production should use
    # DynamoDB plus a private S3 bucket. Local development defaults to a small
    # JSON metadata file and local files so the feature works without AWS.
    KNOWLEDGE_TABLE_NAME = os.getenv("KNOWLEDGE_TABLE_NAME", "").strip()
    KNOWLEDGE_STORAGE_BACKEND = os.getenv(
        "KNOWLEDGE_STORAGE_BACKEND",
        "dynamodb",
    ).strip().lower()
    KNOWLEDGE_FILES_BUCKET = os.getenv("KNOWLEDGE_FILES_BUCKET", "").strip()
    KNOWLEDGE_FILE_STORAGE_BACKEND = os.getenv(
        "KNOWLEDGE_FILE_STORAGE_BACKEND",
        "s3" if KNOWLEDGE_FILES_BUCKET else "local",
    ).strip().lower()
    # Optional bucket-specific credentials. These are useful for Lightsail
    # Object Storage access keys because those keys should not replace the
    # application's normal AWS credentials used for DynamoDB. When omitted,
    # boto3 uses its standard credential chain.
    KNOWLEDGE_S3_ACCESS_KEY_ID = os.getenv(
        "KNOWLEDGE_S3_ACCESS_KEY_ID",
        "",
    ).strip()
    KNOWLEDGE_S3_SECRET_ACCESS_KEY = os.getenv(
        "KNOWLEDGE_S3_SECRET_ACCESS_KEY",
        "",
    ).strip()
    KNOWLEDGE_S3_SESSION_TOKEN = os.getenv(
        "KNOWLEDGE_S3_SESSION_TOKEN",
        "",
    ).strip()
    KNOWLEDGE_LOCAL_METADATA_PATH = os.getenv(
        "KNOWLEDGE_LOCAL_METADATA_PATH",
        "instance/knowledge/metadata.json",
    )
    KNOWLEDGE_LOCAL_STORAGE_DIR = os.getenv(
        "KNOWLEDGE_LOCAL_STORAGE_DIR",
        "instance/knowledge/files",
    )
    KNOWLEDGE_MAX_FILE_BYTES = int(
        os.getenv("KNOWLEDGE_MAX_FILE_BYTES", str(10 * 1024 * 1024))
    )

    JSON_SORT_KEYS = False


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = "http"
    LIVE_QA_STORAGE_BACKEND = os.getenv("LIVE_QA_STORAGE_BACKEND", "memory").strip().lower()
    KNOWLEDGE_STORAGE_BACKEND = os.getenv("KNOWLEDGE_STORAGE_BACKEND", "local").strip().lower()
    MEETING_SHARES_STORAGE_BACKEND = os.getenv(
        "MEETING_SHARES_STORAGE_BACKEND",
        "local",
    ).strip().lower()


class ProductionConfig(BaseConfig):
    DEBUG = False
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()
    REDIS_URL = os.getenv("REDIS_URL", "").strip()

    USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "").strip()
    TRANSCRIPTS_TABLE_NAME = os.getenv("TRANSCRIPTS_TABLE_NAME", "").strip()
    ACTIONS_TABLE_NAME = os.getenv("ACTIONS_TABLE_NAME", "").strip()
    ANALYTICS_TABLE_NAME = os.getenv("ANALYTICS_TABLE_NAME", "").strip()
    MEETING_SHARES_TABLE_NAME = os.getenv("MEETING_SHARES_TABLE_NAME", "").strip()
    LIVE_QA_TABLE_NAME = os.getenv("LIVE_QA_TABLE_NAME", "").strip()
    SUPPORT_REQUESTS_TABLE_NAME = os.getenv("SUPPORT_REQUESTS_TABLE_NAME", "").strip()
    KNOWLEDGE_TABLE_NAME = os.getenv("KNOWLEDGE_TABLE_NAME", "").strip()

    ANALYTICS_STORAGE_BACKEND = os.getenv(
        "ANALYTICS_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    LIVE_QA_STORAGE_BACKEND = os.getenv(
        "LIVE_QA_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    SUPPORT_STORAGE_BACKEND = os.getenv(
        "SUPPORT_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    KNOWLEDGE_STORAGE_BACKEND = os.getenv(
        "KNOWLEDGE_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    KNOWLEDGE_FILES_BUCKET = os.getenv("KNOWLEDGE_FILES_BUCKET", "").strip()
    KNOWLEDGE_FILE_STORAGE_BACKEND = os.getenv(
        "KNOWLEDGE_FILE_STORAGE_BACKEND", "s3"
    ).strip().lower()

    RATE_LIMIT_STORAGE_BACKEND = os.getenv(
        "RATE_LIMIT_STORAGE_BACKEND", "redis"
    ).strip().lower()
    ADMIN_ANALYTICS_CACHE_BACKEND = os.getenv(
        "ADMIN_ANALYTICS_CACHE_BACKEND", "redis"
    ).strip().lower()
    RECORDER_LIVE_STATE_BACKEND = os.getenv(
        "RECORDER_LIVE_STATE_BACKEND", "redis"
    ).strip().lower()
    RECORDER_JOB_STORAGE_BACKEND = os.getenv(
        "RECORDER_JOB_STORAGE_BACKEND", "s3"
    ).strip().lower()
    RECORDER_JOB_QUEUE_BACKEND = os.getenv(
        "RECORDER_JOB_QUEUE_BACKEND", "redis"
    ).strip().lower()
    RECORDER_JOBS_BUCKET = os.getenv("RECORDER_JOBS_BUCKET", "").strip()
    RECORDER_JOB_IDEMPOTENCY_CHECK = True
    TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))


class TestingConfig(BaseConfig):
    TESTING = True
    ANALYTICS_STORAGE_BACKEND = "memory"
    SESSION_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = "http"
    LIVE_QA_STORAGE_BACKEND = "memory"
    ACTIONS_STORAGE_BACKEND = "memory"
    SUPPORT_STORAGE_BACKEND = "memory"
    KNOWLEDGE_STORAGE_BACKEND = "memory"
    KNOWLEDGE_FILE_STORAGE_BACKEND = "local"
    MEETING_SHARES_STORAGE_BACKEND = "memory"
    RECORDER_LIVE_STATE_BACKEND = "memory"
    RECORDER_JOB_STORAGE_BACKEND = "local"
    RECORDER_JOB_QUEUE_BACKEND = "inline"
    RATE_LIMIT_STORAGE_BACKEND = "memory"
    ADMIN_ANALYTICS_CACHE_BACKEND = "memory"
    AI_GLOBAL_DAILY_BUDGET_USD = 0
    AI_GLOBAL_MONTHLY_BUDGET_USD = 0
    AI_USER_DAILY_BUDGET_USD = 0
    AI_USER_DAILY_TRANSCRIPTION_MINUTES = 0
    AI_LIVE_QA_MAX_MINUTES_PER_MEETING = 0
    AI_LIVE_QA_MAX_ANSWERS_PER_MEETING = 0
    AI_MAX_MODEL_PRESET = "advanced"
    CSRF_ENABLED = False
    SUPPORT_EMAIL = ""
    SUPPORT_SMTP_HOST = ""
    SECRET_KEY = "testing-secret-key"


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
