"""Public job-posting discovery through configured source adapters.

The feature finds publicly accessible postings exposed by configured sources. It
cannot guarantee internal, unlisted, removed, authentication-protected, or
otherwise inaccessible positions.
"""

from .models import CompanySource, DiscoveredJob, JobSourceType
from .service import DiscoveryResult, JobDiscoveryService

__all__ = [
    "CompanySource",
    "DiscoveredJob",
    "DiscoveryResult",
    "JobDiscoveryService",
    "JobSourceType",
]
