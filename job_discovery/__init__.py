"""Public job-posting discovery through configured source adapters.

Collected postings are stored as discovery-specific records. They do not become
JobApplications until a separate, explicit promotion action is performed.
"""

from .models import CompanySource, DiscoveredJob, JobFitSnapshot, JobSourceType
from .service import DiscoveryResult, JobDiscoveryService
from .storage import DynamoDBDiscoveryStore

__all__ = [
    "CompanySource",
    "DiscoveredJob",
    "DiscoveryResult",
    "DynamoDBDiscoveryStore",
    "JobDiscoveryService",
    "JobFitSnapshot",
    "JobSourceType",
]
