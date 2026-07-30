# Public job discovery adapters

The job-discovery feature **finds publicly accessible job postings exposed by configured sources**. It does not claim to find literally every job. Internal, unlisted, removed, authentication-protected, or otherwise inaccessible positions cannot be guaranteed.

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

## Configured source examples

```python
from job_discovery.models import CompanySource, JobSourceType

sources = [
    CompanySource(
        source_id="example-greenhouse",
        company_name="Example",
        source_type=JobSourceType.GREENHOUSE,
        identifier="example-board-token",
    ),
    CompanySource(
        source_id="example-lever",
        company_name="Example",
        source_type=JobSourceType.LEVER,
        identifier="example-site",
    ),
    CompanySource(
        source_id="example-ashby",
        company_name="Example",
        source_type=JobSourceType.ASHBY,
        identifier="example-board-name",
        options={"include_compensation": True},
    ),
    CompanySource(
        source_id="example-jsonld",
        company_name="Example",
        source_type=JobSourceType.GENERIC_JSONLD,
        careers_url="https://example.com/careers",
        options={
            "max_pages": 10,
            "timeout_seconds": 10,
            "min_request_interval_seconds": 1,
            "cache_seconds": 900,
        },
    ),
]
```

## Adapter behavior

- **Greenhouse:** calls the public Job Board GET endpoint with `content=true` and maps published jobs.
- **Lever:** calls the public Postings API by company site identifier; EU instances can set `options={"region": "eu"}`.
- **Ashby:** calls the public job-board posting endpoint and requests compensation by default. Jobs marked `isListed=false` are excluded unless `include_unlisted` is explicitly enabled.
- **Generic JSON-LD:** starts at the configured career URL, extracts Schema.org `JobPosting` objects, and can follow a bounded number of same-host job/career links.

The generic connector honors `/robots.txt` using the `ReuniaJobBot` product token, follows the RFC 9309 4xx/5xx access semantics, caches robots policy for no more than 24 hours, limits robots parsing to 500 KiB, uses a descriptive user agent, enforces HTTP timeouts, rate-limits requests per host, limits page size and crawl count, and caches fetched pages.

## Service, deduplication, ranking and storage

`JobDiscoveryService` isolates source failures, replaces persisted jobs source-by-source, deduplicates exact and likely cross-source duplicates, and optionally applies deterministic fit ranking against target titles, verified skills, preferred locations, remote preference and employment type.

`InMemoryJobStore` is suitable for tests. `JsonFileJobStore` is a small local development adapter. Production deployment should provide a `JobStore` implementation backed by durable shared storage such as DynamoDB.
