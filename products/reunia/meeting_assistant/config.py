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

    # Career Bridge storage adapters. Application records always use DynamoDB;
    # production also requires durable workflow and document storage.
    CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", "memory"
    ).strip().lower()
    CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND", "memory"
    ).strip().lower()
    CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND",
        CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND,
    ).strip().lower()
    CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME",
        CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME,
    ).strip()
    CAREER_BRIDGE_ASYNC_JOB_LEASE_SECONDS = int(
        os.getenv("CAREER_BRIDGE_ASYNC_JOB_LEASE_SECONDS", "900")
    )
    CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS = int(
        os.getenv("CAREER_BRIDGE_ASYNC_WORKER_HEARTBEAT_INTERVAL_SECONDS", "15")
    )
    CAREER_BRIDGE_ASYNC_WORKER_MAX_AGE_SECONDS = int(
        os.getenv("CAREER_BRIDGE_ASYNC_WORKER_MAX_AGE_SECONDS", "90")
    )
    CAREER_BRIDGE_ASYNC_WORKER_HEALTH_CACHE_SECONDS = int(
        os.getenv("CAREER_BRIDGE_ASYNC_WORKER_HEALTH_CACHE_SECONDS", "10")
    )
    CAREER_BRIDGE_JOB_DISCOVERY_SLOW_REQUEST_MS = os.getenv(
        "CAREER_BRIDGE_JOB_DISCOVERY_SLOW_REQUEST_MS", "1000"
    ).strip()
    CAREER_BRIDGE_APPLICATIONS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_WORKFLOWS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS = int(
        os.getenv(
            "CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS",
            os.getenv("CAREER_BRIDGE_WORKFLOW_TTL_SECONDS", str(8 * 60 * 60)),
        )
    )
    # Backward-compatible alias. It now controls scratch workflows only.
    CAREER_BRIDGE_WORKFLOW_TTL_SECONDS = CAREER_BRIDGE_SCRATCH_WORKFLOW_TTL_SECONDS
    # Zero retains application-linked workflows until explicit deletion.
    CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS = int(
        os.getenv("CAREER_BRIDGE_APPLICATION_WORKFLOW_TTL_SECONDS", "0")
    )
    CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND", "local"
    ).strip().lower()
    CAREER_BRIDGE_DOCUMENTS_BUCKET = os.getenv(
        "CAREER_BRIDGE_DOCUMENTS_BUCKET", ""
    ).strip()
    CAREER_BRIDGE_DOCUMENTS_PREFIX = os.getenv(
        "CAREER_BRIDGE_DOCUMENTS_PREFIX", "career-bridge"
    ).strip("/")
    CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH = os.getenv(
        "CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH", ""
    ).strip()
    CAREER_BRIDGE_DOCUMENTS_KMS_KEY_ID = os.getenv(
        "CAREER_BRIDGE_DOCUMENTS_KMS_KEY_ID", ""
    ).strip()
    # Narrow emergency/demo escape hatch. This bypasses only the Career Bridge
    # persistence requirement; all other Réunia production safeguards remain active.
    CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION = os.getenv(
        "CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION", "false"
    ).strip().casefold() in {"1", "true", "yes", "on"}
    CAREER_BRIDGE_S3_ACCESS_KEY_ID = os.getenv(
        "CAREER_BRIDGE_S3_ACCESS_KEY_ID", ""
    ).strip()
    CAREER_BRIDGE_S3_SECRET_ACCESS_KEY = os.getenv(
        "CAREER_BRIDGE_S3_SECRET_ACCESS_KEY", ""
    ).strip()
    CAREER_BRIDGE_S3_SESSION_TOKEN = os.getenv(
        "CAREER_BRIDGE_S3_SESSION_TOKEN", ""
    ).strip()

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
    # Adaptive Mock Interview records one short answer at a time.
    SHORT_TRANSCRIPTION_MODEL = os.getenv(
        "SHORT_TRANSCRIPTION_MODEL",
        "gpt-4o-mini-transcribe",
    ).strip()
    SHORT_TRANSCRIPTION_ESTIMATED_SECONDS = float(
        os.getenv("SHORT_TRANSCRIPTION_ESTIMATED_SECONDS", "90")
    )
    AUDIO_TRANSCRIPTION_LANGUAGE = os.getenv(
        "AUDIO_TRANSCRIPTION_LANGUAGE",
        "en",
    ).strip()
    SHORT_AUDIO_MAX_FILE_BYTES = int(
        os.getenv("SHORT_AUDIO_MAX_FILE_BYTES", str(24_000_000))
    )
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(60_000_000)))


    # Cost controls. Values of 0 disable the corresponding limit. Defaults are
    # intentionally conservative and can be raised per deployment.
    AI_ENABLED = os.getenv("AI_ENABLED", "true").strip().lower() == "true"
    AI_GLOBAL_DAILY_BUDGET_USD = float(os.getenv("AI_GLOBAL_DAILY_BUDGET_USD", "10"))
    AI_GLOBAL_MONTHLY_BUDGET_USD = float(os.getenv("AI_GLOBAL_MONTHLY_BUDGET_USD", "100"))
    AI_USER_DAILY_BUDGET_USD = float(os.getenv("AI_USER_DAILY_BUDGET_USD", "2"))
    AI_USER_DAILY_TRANSCRIPTION_MINUTES = float(
        os.getenv("AI_USER_DAILY_TRANSCRIPTION_MINUTES", "180")
    )
    AI_UNPRICED_TEXT_REQUEST_RESERVE_USD = float(
        os.getenv("AI_UNPRICED_TEXT_REQUEST_RESERVE_USD", "0.05")
    )
    AI_UNPRICED_TRANSCRIPTION_RESERVE_PER_MINUTE_USD = float(
        os.getenv("AI_UNPRICED_TRANSCRIPTION_RESERVE_PER_MINUTE_USD", "0.02")
    )
    AI_MAX_OUTPUT_TOKENS_KNOWLEDGE_SEARCH = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_KNOWLEDGE_SEARCH", "700")
    )
    AI_MAX_OUTPUT_TOKENS_MEETING_ANALYSIS = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_MEETING_ANALYSIS", "2600")
    )
    # Application Builder structured-output ceilings. These values are used both
    # for provider requests and conservative pre-request budget reservations.
    AI_MAX_OUTPUT_TOKENS_APPLICATION_BUILDER = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_APPLICATION_BUILDER", "4000")
    )
    AI_MAX_OUTPUT_TOKENS_RESUME_IMPORT = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_RESUME_IMPORT", "3200")
    )
    AI_MAX_OUTPUT_TOKENS_RESUME_JOB_ANALYSIS = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_RESUME_JOB_ANALYSIS", "2400")
    )
    AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_RESUME_TAILORING", "5200")
    )
    AI_MAX_OUTPUT_TOKENS_RESUME_EVIDENCE_REVIEW = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_RESUME_EVIDENCE_REVIEW", "3600")
    )
    AI_MAX_OUTPUT_TOKENS_INTERVIEW_PREPARATION = int(
        os.getenv("AI_MAX_OUTPUT_TOKENS_INTERVIEW_PREPARATION", "4200")
    )
    AI_APPLICATION_BUILDER_MAX_ATTEMPTS = int(
        os.getenv("AI_APPLICATION_BUILDER_MAX_ATTEMPTS", "2")
    )
    AI_APPLICATION_BUILDER_CACHE_SECONDS = int(
        os.getenv(
            "AI_APPLICATION_BUILDER_CACHE_SECONDS",
            os.getenv("AI_RESPONSE_CACHE_SECONDS", "3600"),
        )
    )
    AI_COMBINE_MEETING_ANALYSIS_REQUESTS = (
        os.getenv("AI_COMBINE_MEETING_ANALYSIS_REQUESTS", "true").strip().lower() == "true"
    )
    AI_RESPONSE_CACHE_SECONDS = int(os.getenv("AI_RESPONSE_CACHE_SECONDS", "3600"))

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

    # The public job catalog is centrally curated. Administrators always have
    # management access; optional groups or individual accounts may also add
    # sources, configure the shared schedule, and run manual refreshes.
    JOB_CATALOG_MANAGER_GROUPS = tuple(
        value.strip().lower()
        for value in os.getenv(
            "JOB_CATALOG_MANAGER_GROUPS",
            "job_curators,career_coaches",
        ).split(",")
        if value.strip()
    )
    JOB_CATALOG_MANAGER_USER_IDS = tuple(
        value.strip().lower()
        for value in os.getenv("JOB_CATALOG_MANAGER_USER_IDS", "").split(",")
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
            # Standard direct-API prices per one million tokens. Keep this table
            # overridable through ANALYTICS_AI_PRICING_JSON as provider prices evolve.
            "gpt-5.6-sol": {
                "input": 5.00,
                "cached_input": 0.50,
                "output": 30.00,
            },
            "gpt-5.6-terra": {
                "input": 2.50,
                "cached_input": 0.25,
                "output": 15.00,
            },
            "gpt-5.6-luna": {
                "input": 1.00,
                "cached_input": 0.10,
                "output": 6.00,
            },
            "gpt-5-nano": {
                "input": 0.05,
                "cached_input": 0.005,
                "output": 0.40,
            },
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
    KNOWLEDGE_STORAGE_BACKEND = os.getenv("KNOWLEDGE_STORAGE_BACKEND", "local").strip().lower()


class ProductionConfig(BaseConfig):
    DEBUG = False
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()
    REDIS_URL = os.getenv("REDIS_URL", "").strip()

    # Durable Career Bridge persistence is the production baseline. Local
    # memory/filesystem adapters remain available only when explicitly
    # selected together with the narrow non-durable-storage override.
    CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND", "s3"
    ).strip().lower()

    USERS_TABLE_NAME = os.getenv("USERS_TABLE_NAME", "").strip()
    TRANSCRIPTS_TABLE_NAME = os.getenv("TRANSCRIPTS_TABLE_NAME", "").strip()
    ACTIONS_TABLE_NAME = os.getenv("ACTIONS_TABLE_NAME", "").strip()
    ANALYTICS_TABLE_NAME = os.getenv("ANALYTICS_TABLE_NAME", "").strip()
    SUPPORT_REQUESTS_TABLE_NAME = os.getenv("SUPPORT_REQUESTS_TABLE_NAME", "").strip()
    KNOWLEDGE_TABLE_NAME = os.getenv("KNOWLEDGE_TABLE_NAME", "").strip()
    CAREER_BRIDGE_APPLICATIONS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND = os.getenv(
        "CAREER_BRIDGE_ASYNC_JOB_STORAGE_BACKEND", "dynamodb"
    ).strip().lower()
    CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_ASYNC_JOBS_TABLE_NAME",
        CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME,
    ).strip()
    CAREER_BRIDGE_WORKFLOWS_TABLE_NAME = os.getenv(
        "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME", ""
    ).strip()
    CAREER_BRIDGE_DOCUMENTS_BUCKET = os.getenv(
        "CAREER_BRIDGE_DOCUMENTS_BUCKET", ""
    ).strip()

    ANALYTICS_STORAGE_BACKEND = os.getenv(
        "ANALYTICS_STORAGE_BACKEND", "dynamodb"
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

    # Redis is optional for shared rate limits and analytics/AI response caches.
    RATE_LIMIT_STORAGE_BACKEND = os.getenv(
        "RATE_LIMIT_STORAGE_BACKEND", "memory"
    ).strip().lower()
    ADMIN_ANALYTICS_CACHE_BACKEND = os.getenv(
        "ADMIN_ANALYTICS_CACHE_BACKEND", "memory"
    ).strip().lower()
    TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))


class TestingConfig(BaseConfig):
    TESTING = True
    CAREER_BRIDGE_WORKFLOW_STORAGE_BACKEND = "memory"
    CAREER_BRIDGE_APPLICATION_STORAGE_BACKEND = "dynamodb"
    CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND = "local"
    ANALYTICS_STORAGE_BACKEND = "memory"
    SESSION_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = "http"
    ACTIONS_STORAGE_BACKEND = "memory"
    SUPPORT_STORAGE_BACKEND = "memory"
    KNOWLEDGE_STORAGE_BACKEND = "memory"
    KNOWLEDGE_FILE_STORAGE_BACKEND = "local"
    RATE_LIMIT_STORAGE_BACKEND = "memory"
    ADMIN_ANALYTICS_CACHE_BACKEND = "memory"
    AI_GLOBAL_DAILY_BUDGET_USD = 0
    AI_GLOBAL_MONTHLY_BUDGET_USD = 0
    AI_USER_DAILY_BUDGET_USD = 0
    AI_USER_DAILY_TRANSCRIPTION_MINUTES = 0
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
