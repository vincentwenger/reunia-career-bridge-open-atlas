# Public job discovery adapters and records

The job-discovery feature **finds publicly accessible job postings exposed by configured sources**. It does not claim to find literally every job. Internal, unlisted, removed, authentication-protected, or otherwise inaccessible positions cannot be guaranteed.

Collected postings remain discovery records. They are **not** automatically inserted into Job Applications. A later user action such as **Track application** or **Start application** should explicitly promote one selected discovery record into the existing application workflow.

## Package structure

```text
job_discovery/
├── models.py
├── service.py
├── normalization.py
├── deduplication.py
├── ranking.py
├── storage.py
└── sources/
    ├── base.py
    ├── greenhouse.py
    ├── lever.py
    ├── ashby.py
    └── generic_jsonld.py
```

Every adapter implements the common connector contract:

```python
class JobSource(Protocol):
    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        ...
```

## Discovery-specific records

The discovery boundary has three owner-scoped records:

- `CompanySource`: one configured company/source connector, its source identifier, enabled state, filters, and last successful check time.
- `DiscoveredJob`: one public posting, including stable source/external identifiers, canonical URL, description fingerprint, first/last seen timestamps, and active/inactive status.
- `JobFitSnapshot`: a profile-specific analysis result with score, recommendation, confidence, supported/partial/unsupported requirements, hard blockers, and analysis time.

`DiscoveredJob.id` is deterministic for `(owner_id, source_id, external_job_id)`. Refreshes therefore update the same record. A successful source refresh preserves `first_seen_at`, updates `last_seen_at` for returned postings, and marks previously active postings missing from the new response as `active=False`. Missing jobs are not deleted, so the UI can explain that a previously discovered posting is no longer published.

## Configured source example

```python
from job_discovery.models import CompanySource, JobSourceType

source = CompanySource(
    id="example-greenhouse",
    owner_id="user-123",
    company_name="Example",
    careers_url="https://boards.greenhouse.io/example",
    source_type=JobSourceType.GREENHOUSE,
    source_identifier="example-board-token",
    enabled=True,
    last_checked_at="",
    filters={"location": "Portland"},
)
```

Adapter-specific settings belong in `filters`, including `region`, `include_compensation`, crawler timeouts, rate limits, cache duration, and maximum page count.

## Adapter behavior

- **Greenhouse:** calls the public Job Board GET endpoint with `content=true` and maps published jobs.
- **Lever:** calls the public Postings API by company site identifier; EU instances can set `filters={"region": "eu"}`.
- **Ashby:** calls the public job-board posting endpoint and requests compensation by default. Jobs marked `isListed=false` are excluded unless `include_unlisted` is explicitly enabled.
- **Generic JSON-LD:** starts at the configured career URL, extracts Schema.org `JobPosting` objects, and can follow a bounded number of same-host job/career links.

The generic connector honors `/robots.txt` using the `ReuniaJobBot` product token, follows RFC 9309 4xx/5xx access semantics, caches robots policy for no more than 24 hours, limits robots parsing to 500 KiB, uses a descriptive user agent, enforces HTTP timeouts, rate-limits requests per host, limits page size and crawl count, and caches fetched pages.

## Dedicated DynamoDB table

Production discovery storage uses `DynamoDBDiscoveryStore` and a table separate from the existing application table:

```text
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=career-bridge-job-discovery
```

The table uses:

- partition key: `owner_id` (String)
- sort key: `storage_key` (String)

Item keys are:

```text
SOURCE#<source_id>
JOB#<source_id>#<job_id>
FIT#<job_id>#<profile_fingerprint>
```

This layout keeps every query tenant-scoped and avoids table scans. It also prevents collected postings from changing the existing `ApplicationStore` contract or overcrowding Job Applications.

Example table creation:

```bash
aws dynamodb create-table \
  --table-name career-bridge-job-discovery \
  --attribute-definitions \
      AttributeName=owner_id,AttributeType=S \
      AttributeName=storage_key,AttributeType=S \
  --key-schema \
      AttributeName=owner_id,KeyType=HASH \
      AttributeName=storage_key,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region us-west-2
```

Development and tests can use `InMemoryDiscoveryStore` or `JsonFileDiscoveryStore`. The common `DiscoveryStore` protocol deliberately exposes no `create_application` method.

## Fit snapshots

Deterministic ranking produces both a `RankedJob` response and a persisted `JobFitSnapshot`. The snapshot key includes the candidate-profile fingerprint, so a job can be reanalyzed after the verified profile changes without overwriting the prior profile-specific result.
