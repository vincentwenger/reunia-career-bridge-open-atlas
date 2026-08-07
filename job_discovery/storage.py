from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .models import (
    CompanySource,
    DiscoveredJob,
    DiscoveryJobDisposition,
    DiscoveryJobState,
    DiscoveryResultIndexSummary,
    DiscoveryResultRecord,
    DiscoverySearchPreferences,
    DiscoveryScanSchedule,
    DiscoveryScheduleCadence,
    JobAnalysisRecord,
    JobFitSnapshot,
    JobSourceType,
    PublicJobCatalogStatus,
    WorkplaceType,
    normalize_iso_timestamp,
    utc_now_iso,
)
from .public_catalog import PUBLIC_CATALOG_OWNER_ID, to_public_catalog_job

DISCOVERY_TABLE_CONFIG_KEY = "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME"
_SOURCE_PREFIX = "SOURCE#"
_JOB_PREFIX = "JOB#"
_FIT_PREFIX = "FIT#"
_ANALYSIS_PREFIX = "ANALYSIS#"
_STATE_PREFIX = "STATE#"
_RESULT_PREFIX = "RESULT#"
_RESULT_REVISION_KEY = "RESULT#REVISION"
_PREFERENCES_KEY = "PREFERENCES#SEARCH"
_SCHEDULE_KEY = "PREFERENCES#SCHEDULE"
_PUBLIC_SOURCE_PREFIX = "PUBLIC#SOURCE#"
_PUBLIC_JOB_PREFIX = "PUBLIC#JOB#"
_PUBLIC_LOCK_PREFIX = "PUBLIC#LOCK#"

from .storage_base import (
    CacheStore,
    DiscoveryOptimisticLockError,
    DiscoveryStorageConfigurationError,
    DiscoveryStore,
    InMemoryTTLCache,
)
from .storage_dynamodb import DynamoDBDiscoveryStore
from .storage_json import JsonFileDiscoveryStore
from .storage_memory import InMemoryDiscoveryStore
from . import storage_dynamodb as _storage_dynamodb
from . import storage_json as _storage_json
from . import storage_memory as _storage_memory
from . import storage_serialization as _storage_serialization

_STORAGE_SERIALIZATION_EXPORTS = _storage_serialization.exports()
globals().update(_STORAGE_SERIALIZATION_EXPORTS)

for _module in (
    _storage_serialization,
    _storage_memory,
    _storage_json,
    _storage_dynamodb,
):
    _module.activate(globals())
