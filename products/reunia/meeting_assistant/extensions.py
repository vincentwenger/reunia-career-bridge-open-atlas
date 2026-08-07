"""Application-level extension initialization."""
from __future__ import annotations

from flask import Flask

import redis

from job_discovery.storage import DynamoDBDiscoveryStore, InMemoryDiscoveryStore

from meeting_assistant.repositories.action_repository import (
    DynamoActionRepository,
    InMemoryActionRepository,
)
from meeting_assistant.repositories.analytics_repository import (
    DynamoAnalyticsRepository,
    InMemoryAnalyticsRepository,
)
from meeting_assistant.repositories.knowledge_file_store import (
    LocalKnowledgeFileStore,
    S3KnowledgeFileStore,
)
from meeting_assistant.repositories.knowledge_repository import (
    DynamoKnowledgeRepository,
    InMemoryKnowledgeRepository,
    LocalKnowledgeRepository,
)
from meeting_assistant.repositories.support_repository import (
    DynamoSupportRepository,
    InMemorySupportRepository,
)
from meeting_assistant.services.security_service import (
    MemoryRateLimiter,
    MemoryTTLCache,
    RedisRateLimiter,
    RedisTTLCache,
)



def _initialize_shared_infrastructure(app: Flask) -> None:
    redis_backends = {
        str(app.config.get("RATE_LIMIT_STORAGE_BACKEND", "")).lower(),
        str(app.config.get("ADMIN_ANALYTICS_CACHE_BACKEND", "")).lower(),
    }
    redis_client = None
    if "redis" in redis_backends:
        redis_url = str(app.config.get("REDIS_URL") or "").strip()
        if not redis_url:
            raise RuntimeError("REDIS_URL is required for Redis-backed application services.")
        redis_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=10,
            health_check_interval=30,
        )
        try:
            redis_client.ping()
        except redis.RedisError as exc:
            if app.testing or app.debug:
                app.logger.warning("Redis is unavailable; using local development fallbacks: %s", exc)
                redis_client = None
            else:
                raise RuntimeError("Redis could not be reached during application startup.") from exc
    app.extensions["redis_client"] = redis_client

    rate_backend = str(app.config.get("RATE_LIMIT_STORAGE_BACKEND", "memory")).lower()
    if rate_backend == "redis":
        if redis_client is None:
            raise RuntimeError("Redis rate limiting requires an available Redis connection.")
        app.extensions["rate_limiter"] = RedisRateLimiter(redis_client)
    elif rate_backend == "memory":
        app.extensions["rate_limiter"] = MemoryRateLimiter()
    else:
        raise RuntimeError("RATE_LIMIT_STORAGE_BACKEND must be 'memory' or 'redis'.")

    cache_backend = str(app.config.get("ADMIN_ANALYTICS_CACHE_BACKEND", "memory")).lower()
    if cache_backend == "redis":
        if redis_client is None:
            raise RuntimeError("Redis analytics caching requires an available Redis connection.")
        app.extensions["admin_analytics_cache"] = RedisTTLCache(redis_client, prefix="reunia:admin-analytics")
        app.extensions["ai_response_cache"] = RedisTTLCache(redis_client, prefix="reunia:ai-response")
    elif cache_backend == "memory":
        app.extensions["admin_analytics_cache"] = MemoryTTLCache()
        app.extensions["ai_response_cache"] = MemoryTTLCache()
    else:
        raise RuntimeError("ADMIN_ANALYTICS_CACHE_BACKEND must be 'memory' or 'redis'.")


def init_extensions(app: Flask) -> None:
    _initialize_shared_infrastructure(app)

    discovery_backend = str(
        app.config.get("CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND", "memory")
    ).strip().lower()
    if discovery_backend == "dynamodb":
        discovery_store = DynamoDBDiscoveryStore(app.config)
    elif discovery_backend == "memory":
        discovery_store = InMemoryDiscoveryStore()
    else:
        raise RuntimeError(
            "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND must be either "
            "'memory' or 'dynamodb'."
        )
    app.extensions["career_bridge_job_discovery_store"] = discovery_store

    analytics_backend = str(
        app.config.get("ANALYTICS_STORAGE_BACKEND", "memory")
    ).strip().lower()
    if analytics_backend == "dynamodb":
        analytics_repository = DynamoAnalyticsRepository()
    elif analytics_backend == "memory":
        analytics_repository = InMemoryAnalyticsRepository()
    else:
        raise RuntimeError(
            "ANALYTICS_STORAGE_BACKEND must be either 'memory' or 'dynamodb'."
        )
    app.extensions["analytics_repository"] = analytics_repository
    if analytics_backend == "dynamodb":
        app.logger.info(
            "Admin analytics persistence: DynamoDB table %s in %s",
            app.config["ANALYTICS_TABLE_NAME"],
            app.config["AWS_REGION"],
        )
    else:
        app.logger.warning(
            "Admin analytics persistence is in memory; activity will be lost on restart."
        )

    actions_backend = str(
        app.config.get("ACTIONS_STORAGE_BACKEND", "dynamodb")
    ).strip().lower()
    if actions_backend == "dynamodb":
        action_repository = DynamoActionRepository()
    elif actions_backend == "memory":
        action_repository = InMemoryActionRepository()
    else:
        raise RuntimeError(
            "ACTIONS_STORAGE_BACKEND must be either 'memory' or 'dynamodb'."
        )

    app.extensions["action_repository"] = action_repository
    if actions_backend == "dynamodb":
        app.logger.info(
            "Career Action Plan persistence: DynamoDB table %s in %s",
            app.config["ACTIONS_TABLE_NAME"],
            app.config["AWS_REGION"],
        )
    else:
        app.logger.warning(
            "Career Action Plan persistence is in memory; changes will be lost on restart."
        )

    support_backend = str(
        app.config.get("SUPPORT_STORAGE_BACKEND", "dynamodb")
    ).strip().lower()
    if support_backend == "dynamodb":
        support_repository = DynamoSupportRepository()
    elif support_backend == "memory":
        support_repository = InMemorySupportRepository()
    else:
        raise RuntimeError(
            "SUPPORT_STORAGE_BACKEND must be either 'memory' or 'dynamodb'."
        )

    app.extensions["support_repository"] = support_repository
    if support_backend == "dynamodb":
        app.logger.info(
            "Help & Support persistence: DynamoDB table %s in %s",
            app.config["SUPPORT_REQUESTS_TABLE_NAME"],
            app.config["AWS_REGION"],
        )
    else:
        app.logger.warning(
            "Help & Support persistence is in memory; requests will be lost on restart."
        )
    # Production uses the Redis-backed application limiter, so support request
    # throttles are shared across every Gunicorn worker.
    app.extensions["support_rate_limiter"] = app.extensions["rate_limiter"]

    knowledge_backend = str(
        app.config.get("KNOWLEDGE_STORAGE_BACKEND", "memory")
    ).strip().lower()
    if knowledge_backend == "dynamodb":
        knowledge_repository = DynamoKnowledgeRepository()
    elif knowledge_backend == "local":
        knowledge_repository = LocalKnowledgeRepository(
            app.config.get(
                "KNOWLEDGE_LOCAL_METADATA_PATH",
                "instance/knowledge/metadata.json",
            )
        )
    elif knowledge_backend == "memory":
        knowledge_repository = InMemoryKnowledgeRepository()
    else:
        raise RuntimeError(
            "KNOWLEDGE_STORAGE_BACKEND must be 'dynamodb', 'local', or 'memory'."
        )
    app.extensions["knowledge_repository"] = knowledge_repository

    knowledge_file_backend = str(
        app.config.get("KNOWLEDGE_FILE_STORAGE_BACKEND", "local")
    ).strip().lower()
    if knowledge_file_backend == "s3":
        bucket = str(app.config.get("KNOWLEDGE_FILES_BUCKET") or "").strip()
        if not bucket:
            raise RuntimeError(
                "KNOWLEDGE_FILES_BUCKET is required when KNOWLEDGE_FILE_STORAGE_BACKEND=s3."
            )
        access_key_id = str(
            app.config.get("KNOWLEDGE_S3_ACCESS_KEY_ID") or ""
        ).strip()
        secret_access_key = str(
            app.config.get("KNOWLEDGE_S3_SECRET_ACCESS_KEY") or ""
        ).strip()
        if bool(access_key_id) != bool(secret_access_key):
            raise RuntimeError(
                "KNOWLEDGE_S3_ACCESS_KEY_ID and "
                "KNOWLEDGE_S3_SECRET_ACCESS_KEY must be configured together."
            )
        knowledge_file_store = S3KnowledgeFileStore(
            bucket,
            app.config["AWS_REGION"],
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=str(
                app.config.get("KNOWLEDGE_S3_SESSION_TOKEN") or ""
            ).strip(),
        )
    elif knowledge_file_backend == "local":
        knowledge_file_store = LocalKnowledgeFileStore(
            app.config.get(
                "KNOWLEDGE_LOCAL_STORAGE_DIR",
                "instance/knowledge/files",
            )
        )
    else:
        raise RuntimeError(
            "KNOWLEDGE_FILE_STORAGE_BACKEND must be either 's3' or 'local'."
        )
    app.extensions["knowledge_file_store"] = knowledge_file_store

    if knowledge_backend == "dynamodb":
        app.logger.info(
            "Document Library metadata: DynamoDB table %s in %s",
            app.config["KNOWLEDGE_TABLE_NAME"],
            app.config["AWS_REGION"],
        )
    elif knowledge_backend == "local":
        app.logger.info(
            "Document Library metadata: local file %s",
            app.config.get(
                "KNOWLEDGE_LOCAL_METADATA_PATH",
                "instance/knowledge/metadata.json",
            ),
        )
    else:
        app.logger.warning(
            "Document Library metadata is in memory and will be lost on restart."
        )

    if knowledge_file_backend == "s3":
        app.logger.info(
            "Document Library files: private S3 bucket %s",
            app.config["KNOWLEDGE_FILES_BUCKET"],
        )
    else:
        app.logger.warning(
            "Document Library files use local storage at %s. Use S3 in production "
            "to survive container replacement.",
            app.config.get(
                "KNOWLEDGE_LOCAL_STORAGE_DIR",
                "instance/knowledge/files",
            ),
        )
