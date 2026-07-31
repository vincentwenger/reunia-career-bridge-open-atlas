"""Public job-posting discovery through configured source adapters.

Collected postings are stored as discovery-specific records. They do not become
JobApplications until a separate, explicit promotion action is performed.
"""

from .application_conversion import (
    ApplicationWorkspaceResult,
    DiscoveredJobApplicationService,
)
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
    EvidenceReference,
    JobAnalysisRecord,
    JobFitSnapshot,
    JobSourceType,
    PublicJobCatalogStatus,
    RequirementEvidenceMatch,
)
from .ranking import (
    SEARCH_PRIORITY_FORMULA,
    CandidateJobProfile,
    PreferenceScoreComponent,
    RankedJob,
    StageOneEvaluation,
)
from .scheduling import (
    ExternalJobDiscoveryRunner,
    ScheduledScanSummary,
    next_scheduled_run,
    schedule_is_due,
)
from .service import DiscoveryResult, JobDiscoveryService
from .storage import DynamoDBDiscoveryStore

__all__ = [
    "ApplicationWorkspaceResult",
    "CandidateJobProfile",
    "CompanySource",
    "DiscoveredJob",
    "DiscoveredJobApplicationService",
    "DiscoveryJobDisposition",
    "DiscoveryJobState",
    "DiscoveryResultIndexSummary",
    "DiscoveryResultRecord",
    "DiscoverySearchPreferences",
    "DiscoveryScanSchedule",
    "DiscoveryScheduleCadence",
    "DiscoveryResult",
    "DynamoDBDiscoveryStore",
    "EvidenceReference",
    "ExternalJobDiscoveryRunner",
    "JobAnalysisRecord",
    "JobDiscoveryService",
    "JobFitSnapshot",
    "JobSourceType",
    "PreferenceScoreComponent",
    "PublicJobCatalogStatus",
    "RequirementEvidenceMatch",
    "RankedJob",
    "SEARCH_PRIORITY_FORMULA",
    "ScheduledScanSummary",
    "StageOneEvaluation",
    "next_scheduled_run",
    "schedule_is_due",
]
