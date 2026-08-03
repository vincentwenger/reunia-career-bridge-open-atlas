# Public job discovery adapters and records

The job-discovery feature **finds publicly accessible job postings exposed by configured sources**. It does not claim to find literally every job. Internal, unlisted, removed, authentication-protected, or otherwise inaccessible positions cannot be guaranteed.

Collected postings remain discovery records. They are **not** automatically inserted into Job Applications. The user must explicitly choose **Create Application Workspace** for one selected result before the existing application workflow is created.

The **Public job discovery** result panel also provides view-only **Country** and **U.S. state** filters. The first visit defaults the country to **United States**; selecting **All countries** explicitly removes that default. Country values use stable two-letter codes in the URL, while displayed labels remain user friendly. The U.S. state control is enabled only while the selected country is the United States. State matching recognizes abbreviations and full names in normalized source locations. Selecting a U.S. state includes nationwide U.S.-remote postings that identify the United States but do not name a state. Postings whose location cannot be identified are not guessed into a country or state and remain visible when those filters are unset. The controls are visually grouped into match-quality and location/sort sections, with responsive layouts for tablet and mobile widths.

## Package structure

```text
job_discovery/
├── models.py
├── application_conversion.py
├── service.py
├── normalization.py
├── location_filter.py
├── deduplication.py
├── ranking.py
├── result_policy.py
├── source_import.py
├── storage.py
└── sources/
    ├── base.py
    ├── greenhouse.py
    ├── lever.py
    ├── ashby.py
    ├── workday.py
    └── generic_jsonld.py
```

Every adapter implements the common connector contract:

```python
class JobSource(Protocol):
    def fetch_jobs(self, source: CompanySource) -> list[DiscoveredJob]:
        ...
```

## Discovery-specific records

The discovery boundary has eight owner-scoped record types:

- `CompanySource`: one user-managed company/source connector, its source identifier or public career-page URL, enabled state, filters, last successful check time, and an optimistic-concurrency `revision`.
- `DiscoverySearchPreferences`: the owner-managed desired titles, preferred locations, accepted workplace types, employment types, salary floor, preferred keywords, required keywords, posting-wide excluded terms, job-title-only excluded terms, maximum posting age, and mandatory-filter switches used by Stage 1 and Search Priority. Positive keywords are posting preferences only and never become candidate evidence. The posting-age preference defaults to 30 days and applies uniformly after every connector has normalized its records.
- `DiscoveryScanSchedule`: the owner-managed manual, daily, or weekly scan cadence, local hour, weekday, IANA time zone, and last external run time. It is consumed only by the external runner.
- `DiscoveredJob`: one public posting, including stable source/external identifiers, canonical URL, description fingerprint, first/last seen timestamps, and active/inactive status.
- `JobFitSnapshot`: an evidence-profile-specific **Job Fit** result with score, recommendation, confidence, supported/partial/unsupported requirements, hard blockers, record-level evidence matches, and analysis time. Each displayable strength contains a `RequirementEvidenceMatch` with one or more `EvidenceReference` values identifying the exact Career Profile or verified Evidence Library record. It deliberately does not store location, salary, workplace, employment-type, desired-title, freshness, or Search Priority values.
- `DiscoveryJobState`: the explicit user disposition for one posting (`saved`, `ignored`, or `application_created`) and, after conversion, the linked application ID.
- `DiscoveryResultIndexSummary`: one profile, preference, result-filter, and sort-specific materialized-index header containing category counts, filtered counts, build time, and the discovery revision token used for invalidation.
- `DiscoveryResultRecord`: one compact pre-ranked result card. It stores the recommendation tier, confidence tier, visibility category, ordinal rank, and only the fields needed by the paginated list, omitting full job descriptions, skills, metadata, requirement arrays, and evidence references.

`DiscoveredJob.id` is deterministic for `(owner_id, source_id, external_job_id)`. Refreshes therefore update the same record. A successful source refresh preserves `first_seen_at`, updates `last_seen_at` for returned postings, resets `missed_scan_count` to zero, and increments the counter for missing postings. A posting remains active through transient misses and is marked inactive after three consecutive successful scans by default. `filters={"deactivate_after_missed_scans": N}` can select a bounded threshold from two through ten. Missing jobs are not deleted, so the UI can explain that a previously discovered posting is no longer published.

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

## Bulk company-source import

Administrators and configured catalog-manager groups can populate the shared **Company sources** panel by uploading a UTF-8 CSV or JSON configuration file. The import accepts at most 100 companies and 1 MB per file, validates every row before any source is written, and supports either skipping duplicates or updating the matching source. Downloadable CSV and JSON examples are available beside the upload control. Managers can also use **Remove all company sources** after a destructive-action confirmation. That action removes only the shared source configurations; previously collected postings, saved jobs, and Application Workspaces remain intact. A source-count check prevents a stale settings page from deleting sources that were added after the page loaded.

Each company entry also provides **Scan this source**, which runs the same bounded shared-catalog refresh pipeline for only that enabled company. The card then reopens with its persisted **Last scan result**. Managers can see whether the latest scan succeeded, completed within browser-safe limits, has not run yet, or produced an issue. The panel includes the last-attempt timestamp, the exact sanitized issue message (for example a robots.txt denial), the previous successful-scan timestamp when cached jobs remain available, and the number of active public postings retained from the latest successful scan.

CSV uses these user-facing headers:

```csv
Company,Source type,ATS site identifier,Career-page URL,Enabled
Intel,Workday,,https://intel.wd1.myworkdayjobs.com/External,true
```

JSON may be a direct array or an object containing a `companies` array:

```json
{
  "companies": [
    {
      "company": "Intel",
      "source_type": "Workday",
      "ats_site_identifier": "",
      "career_page_url": "https://intel.wd1.myworkdayjobs.com/External",
      "enabled": true
    }
  ]
}
```

Source-type names are case-insensitive. Supported values are Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, SmartRecruiters, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, PeopleAdmin, Radancy / TalentBrew, Amazon Jobs, Branded Requisition Portal, and Manual career-page URL. Workday URLs are canonicalized before duplicate matching, so locale variants of the same board do not create separate sources. Invalid rows are reported with their CSV/JSON row number and the panel is left unchanged.

## Source-independent posting-age policy

Job Discovery applies the same freshness rules to **Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, SmartRecruiters, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, PeopleAdmin, Radancy / TalentBrew, Amazon Jobs, Branded Requisition Portal, and Manual JSON-LD** records after connector normalization. Normalized public postings are retained in the shared catalog before an individual user's age filter is applied. Each user has a private `DiscoverySearchPreferences.maximum_posting_age_days` setting, defaulting to **30 days**, that controls which centrally collected postings appear in that user's results. The UI offers 7, 14, 30, 60, or Any age.

A posting is retained when any of the following is true:

- its normalized posting or update timestamp is within the selected age window;
- the source does not expose a usable posting date, in which case the result remains eligible and is labeled as date unknown;
- it has an explicit `valid_through` or application-closing timestamp in the future; or
- the normalized source metadata explicitly marks it evergreen or continuous hiring.

A user's private age preference is applied while the shared catalog is materialized into that user's discovery records and before any new fit analysis. A restrictive preference therefore hides older postings for that user without removing them from the shared public catalog. Historical records are retained rather than deleted. Saved postings and postings already converted into Application Workspaces remain visible even when they later exceed the user's configured age window. Manual administrator refreshes and external scheduled scans populate the same central catalog.

## Adapter behavior

- **Greenhouse:** calls the public Job Board GET endpoint with `content=true` and maps published jobs.
- **Lever:** calls the public Postings API by company site identifier; EU instances can set `filters={"region": "eu"}`.
- **Ashby:** calls the public job-board posting endpoint and requests compensation by default. Jobs marked `isListed=false` are always excluded; source configuration cannot opt into unlisted records.
- **Workday:** accepts a public `myworkdayjobs.com` or `myworkdaysite.com` board URL, derives the tenant and career-site identifiers, paginates the same public CXS JSON endpoint used by the career page, and enriches each listing from its public detail endpoint. Closed records with `canApply=false` are excluded. This is a public-career-site integration, not an authenticated or versioned Workday tenant API, so it must fail visibly if Workday changes the public response contract.
- **SAP SuccessFactors:** accepts a public Career Site Builder URL on an SAP-hosted or employer-owned domain, normalizes the URL to the public search page, follows bounded result pagination, and extracts normalized details from public job pages and schema.org `JobPosting` data. Interactive refreshes defer excess detail pages, while scheduled scans can complete the catalog.
- **Oracle Cloud HCM:** accepts a public Oracle Recruiting Candidate Experience jobs URL such as `/hcmUI/CandidateExperience/en/sites/CX_1/jobs` or a vanity-domain equivalent. It derives the site and language, uses the unauthenticated Candidate Experience requisition collection/detail resources loaded by the public career site, follows bounded `limit`/`offset` pagination, and maps Oracle requisition descriptions, qualifications, responsibilities, locations, dates, workplace type, employment type, department, and skills. If those resources are unavailable on an older or vanity-domain deployment, it falls back to same-host public HTML, embedded Oracle JSON, and schema.org `JobPosting` data. Oracle documents the CE resources as internal-use interfaces, so this is a best-effort public-career-site integration rather than a supported tenant API and it fails visibly if Oracle changes the contract. No tenant credentials or authenticated recruiting APIs are used.
- **iCIMS:** accepts a public iCIMS career portal URL on an `icims.com` hostname. It normalizes classic `/jobs/search` portals, branded listing paths ending in `/jobs`, and direct numeric `/jobs/<id>` job URLs. The connector follows bounded public HTML pagination, maps listing-card fields, and enriches a configurable number of postings from public detail pages and schema.org `JobPosting` data. It does not use customer credentials, candidate APIs, or application endpoints.
- **Jobvite:** accepts hosted `jobs.jobvite.com/<career-site>` boards, searches, and public job-detail URLs. It normalizes them to the hosted search page, follows bounded pagination, and retrieves complete public descriptions without requiring the paid Job Feed API.
- **PeopleAdmin:** accepts standard `*.peopleadmin.com` boards and institution-branded public hosts. Root, `/postings/search`, paginated search, and `/postings/<id>` URLs normalize to the same public listing. It follows bounded `page=` pagination, enriches postings from their public detail pages, and never accesses applicant accounts or authenticated HR workflows.
- **Radancy / TalentBrew:** accepts employer-branded public career sites, including root pages, `/search-jobs`, filtered `/location/...` or `/category/...` pages, and `/job/<location>/<slug>/<site-id>/<job-id>` detail URLs. It normalizes the configured host to its public search page, preserves visible locale prefixes, follows bounded public pagination, and enriches descriptions from the branded detail pages. Requests remain restricted to the exact configured hostname because TalentBrew sites normally use employer-owned domains.
- **Amazon Jobs:** accepts only the official `amazon.jobs` host. Root, locale search, filtered search, and `/jobs/<job-id>/<slug>` URLs normalize to one locale-aware public search catalog. The connector preserves public search filters such as country or region, performs bounded `offset`/`result_limit` pagination, and enriches postings from official Amazon job-detail pages. It does not access employee-only jobs, candidate accounts, or Amazon’s separate hourly-hiring application system.
- **Branded Requisition Portal:** accepts employer-owned public sites that expose an HTML requisition feed at `/api/requisitions/search` and public details under `/job/<id>/<slug>`. Root, `/search-jobs`, feed, paginated feed, and detail URLs normalize to the same feed while preserving non-pagination public search selectors. Fetches remain restricted to the exact configured hostname, follow bounded `page=` pagination, and enrich full descriptions from public job pages. The label intentionally avoids claiming an unverified ATS vendor.
- **Generic JSON-LD:** starts at the configured career URL, extracts Schema.org `JobPosting` objects, and can follow a bounded number of same-host job/career links.

The generic connector honors `/robots.txt` using the `ReuniaJobBot` product token, follows RFC 9309 4xx/5xx access semantics, caches robots policy for no more than 24 hours, limits robots parsing to 500 KiB, uses a descriptive user agent, enforces HTTP timeouts, rate-limits requests per configured company source, limits page size and crawl count, and caches fetched pages.

## Fetch and lifecycle safeguards

All adapters share the following safeguards:

- **Allowed-domain and SSRF controls:** Greenhouse, Lever, and Ashby API requests use fixed exact-domain allowlists. Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, PeopleAdmin, Radancy / TalentBrew, Amazon Jobs, and Branded Requisition Portal requests are restricted to the exact configured public career-site hostname. Generic crawling is restricted to the exact configured career-page hostname. Credentialed URLs, localhost, private/reserved IP literals, cross-domain redirects, and DNS results containing non-public addresses are blocked by the production HTTP client.
- **Bounded HTTP:** source timeouts are clamped to 1–30 seconds. JSON responses default to 4 MiB, HTML pages to 5 MiB, and no configured response limit may exceed 10 MiB. Declared `Content-Length` and streamed bytes are both checked.
- **Redirect limits:** requests allow three redirects by default and at most five. Every redirect target is revalidated against the source allowlist and public-address policy.
- **HTML sanitization:** externally supplied HTML fragments are converted to bounded plain text. Script, style, template, iframe, object, SVG, control-character, and markup content is not persisted as a job description or displayed as executable HTML.
- **Robots and source policy:** generic crawling cannot disable robots checks. Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, SmartRecruiters, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, PeopleAdmin, Amazon Jobs, and Branded Requisition Portal use public posting endpoints or public career-site pages; Ashby unlisted records and Workday records that explicitly report `canApply=false` are rejected.
- **Per-company rate limiting:** all requests are spaced by owner/company key, including robots and detail-page requests. `min_request_interval_seconds` is bounded from zero to sixty seconds.
- **Stable identity:** every description receives a SHA-256 fingerprint; URLs are normalized for scheme/host/default ports, dot segments, tracking parameters, query ordering, and fragments.
- **Deduplication:** records merge transitively by `(source_id, external_job_id)`, canonical URL, or a non-trivial identical content fingerprint.
- **Conservative deactivation:** postings deactivate only after several consecutive successful scans omit them; a reappearing posting is reactivated and its miss counter resets.
- **No automatic submission:** Career Bridge can save or ignore a result, or explicitly create an internal Application Workspace. It has no employer-form submission, document upload, or auto-apply capability.

Useful bounded source options are:

```python
filters={
    "timeout_seconds": 10,
    "max_response_bytes": 4 * 1024 * 1024,
    "max_redirects": 3,
    "min_request_interval_seconds": 1.0,
    "deactivate_after_missed_scans": 3,
}
```

A Workday source uses the public board URL and may additionally configure bounded collection controls:

```python
source = CompanySource(
    id="intel-workday",
    owner_id="user-123",
    company_name="Intel",
    careers_url="https://intel.wd1.myworkdayjobs.com/en-US/External",
    source_type=JobSourceType.WORKDAY,
    source_identifier="External",  # auto-detected by the web form
    filters={
        "locale": "en-US",
        "page_size": 20,   # Workday public endpoint maximum
        "max_pages": 50,
        "max_jobs": 500,
        "search_text": "",  # optional server-side Workday keyword
        "applied_facets": {},
        "detail_fetch_limit": 500,  # full external scan; browser refresh uses a smaller temporary cap
        "fetch_budget_seconds": 0,  # unlimited externally; browser refresh applies a deadline
    },
)
```

Workday list responses are intentionally thin. Full external scans may make one bounded detail request per discovered posting to obtain the description, actual locations, employment type, remote type, requisition ID, and apply status. The interactive Flask refresh instead applies temporary browser-safe limits: at most 80 listing records, four listing pages, ten detail requests, an 18-second connector budget, five-second HTTP timeouts, and no new AI analyses inside the gateway request. Listings whose details are deferred are still persisted with the public title, location, requisition fields, and canonical URL, and appear as **Awaiting assessment**. Saved source settings are not changed by these temporary limits. DynamoDB posting writes are batched when the production table supports `batch_writer`.

## Shared public-job catalog

Public company postings are shared across users so equivalent company sources are not scanned repeatedly. The shared layer contains only public source and posting data. Career Profile information, Job Fit, confidence, recommendation, saved/ignored state, and Application Workspaces remain owner-scoped.

The browser and external scheduler both use the same flow:

1. Canonicalize the public source. Equivalent Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, SmartRecruiters, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, PeopleAdmin, Amazon Jobs, Branded Requisition Portal, or JSON-LD configurations resolve to one stable source key. Workday locale URL variants such as `/External` and `/en-US/External` resolve to the same tenant/site catalog.
2. Reuse the shared catalog while it is fresh. Defaults are six hours for Greenhouse, Lever, and Ashby; twelve hours for Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, Avature, Eightfold, Taleo, Dayforce, Talemetry / TTC Portals, Jobvite, UKG Pro / UltiPro, and PeopleAdmin; six hours for SmartRecruiters and Amazon Jobs; twelve hours for Branded Requisition Portal; and twenty-four hours for generic JSON-LD pages.
3. Acquire a source-level DynamoDB refresh lock before making external requests. Another user receives the existing public jobs instead of starting a duplicate scan. Locks expire automatically and use token-checked release.
4. Store normalized postings once under the reserved public catalog partition. Owner-scoped posting records are materialized from those public records, preserving private job IDs and state.
5. Job Discovery renders immediately from the user's durable result records. After rendering, a separate CSRF-protected request copies any newer shared catalog records into that user's discovery records without external HTTP access; the browser reloads only when those records changed.

A source can override the bounded freshness interval with `public_cache_ttl_seconds`, or opt out with `public_catalog_enabled=false`. Public sharing is enabled by default for supported public career sources. Interactive Workday scans remain intentionally partial; partial scans merge new/updated postings without deactivating catalog records that were outside the browser request budget. Full scheduled scans retain the normal conservative missed-scan deactivation policy.

## Dedicated DynamoDB table

Production discovery storage uses `DynamoDBDiscoveryStore` and a table separate from the existing application table:

```text
CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=career-bridge-job-discovery
JOB_DISCOVERY_AI_MODEL=gpt-5-nano
JOB_DISCOVERY_AI_REASONING_EFFORT=minimal
JOB_DISCOVERY_AI_MAX_OUTPUT_TOKENS=4800
```

The table uses:

- partition key: `owner_id` (String)
- sort key: `storage_key` (String)

Item keys are:

```text
SOURCE#<source_id>
JOB#<source_id>#<job_id>
STATE#<source_id>#<job_id>
PREFERENCES#SEARCH
PREFERENCES#SCHEDULE
ANALYSIS#<job_id>#<description_fingerprint>
FIT#<job_id>#<profile_fingerprint>#<description_fingerprint>
RESULT#REVISION
RESULT#<evidence_fingerprint>#<preference_fingerprint>#META
RESULT#<evidence_fingerprint>#<preference_fingerprint>#GROUP#<tab>#<ordinal>#<source_id>#<job_id>

# Reserved owner_id = __PUBLIC_JOB_CATALOG__
PUBLIC#SOURCE#<canonical_source_key>
PUBLIC#JOB#<canonical_source_key>#<public_job_id>
PUBLIC#LOCK#<canonical_source_key>
```

The `RESULT` records are a materialized read model. Opening a valid cached result page performs one summary `GetItem` and one bounded sort-key range query for exactly the requested 10, 20, or 50 cards. It no longer queries every `JOB`, `STATE`, and `FIT` item on every page load. Job, state, fit, source, or search-preference writes replace the owner revision token, making the current materialized index stale. The initial `GET /job-discovery` never rebuilds that index: it immediately serves the current materialized page, or the last materialized page with an updating marker. A separate CSRF-protected prebuild request performs the full job/state/fit scan and reloads after the durable index is ready. Shared-catalog hydration prebuilds the default result policy before it reports a change; browser-driven multi-source refresh and multi-job assessment prebuild once after the entire run instead of once per source or assessed job. Save, ignore, preference, and no-JavaScript mutation flows also prebuild before redirecting. The index fingerprint includes its schema version, candidate evidence, search preferences, posting-age rule, minimum Job Fit, selected confidence tiers, recommendation filter, allowed source IDs, and sort mode. This prevents a pre-ranked page built for one result policy from being reused for another. Old materialized records are removed when a replacement index is committed. Full descriptions and evidence remain only in the canonical job and fit records and are loaded on demand by **View analysis** or workspace creation. On cached pages, Application Workspace records are also hydrated only for visible cards that contain an `application_id`; the route no longer lists every application for the owner on each result-page request.

This read model uses the existing `owner_id` and `storage_key` keys, so it requires no GSI, table recreation, or data migration.

This layout avoids table scans. User records remain tenant-scoped, while the reserved public-catalog partition contains only public source metadata, normalized public postings, and short-lived refresh locks. Collected postings and user dispositions stay in the dedicated discovery table, so Job Applications are not overcrowded. `CompanySource` writes use DynamoDB conditional expressions against the stored `revision`; a stale source configuration raises `DiscoveryOptimisticLockError` rather than overwriting a newer update. The existing `ApplicationStore` is extended only with the optional `source_job_id` promotion link and `find_by_source_job(...)` lookup; existing callers can continue omitting that field.

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

## User-ready discovery workflow

Job Discovery is a dedicated destination under **Build Your Application**, immediately before **Job Applications**. The route is `/applications/job-discovery`; Job Applications remains focused on workspaces already selected for pursuit. The catalog is now shared across the product:

- administrators and configured job curators add Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, and manual JSON-LD company sources;
- the same privileged users edit, enable, disable, or remove sources and control the shared manual/daily/weekly external schedule;
- one refresh publishes the collected postings to every user, regardless of companies an individual follows or saves;
- regular users cannot trigger source scans or alter the catalog;
- each user privately saves desired-title, location, remote/hybrid/onsite, employment-type, salary, preferred-keyword, required-keyword, posting-wide excluded-term, job-title-only excluded-term, and posting-age preferences;
- preferred keywords remain a transparent Preference Fit component;
- required keywords and other mandatory preferences hide postings before an AI call; and
- Job Fit, saved/ignored state, and Application Workspace links remain owner-scoped.

Catalog management access is granted to administrators, `JOB_CATALOG_MANAGER_USER_IDS`, or users whose account groups intersect `JOB_CATALOG_MANAGER_GROUPS`. The default approved groups are `job_curators` and `career_coaches`.

The initial results view displays **10 compact opportunities per page** by default, with user-selectable page sizes of 10, 20, or 50. Classification and ordering are materialized once per current profile, preference set, result-filter combination, sort mode, and discovery revision. DynamoDB page requests then use the ordinal embedded in the result sort key to fetch only the requested page range. Results are separated into **Recommended**, **Possible matches**, **Awaiting assessment**, **Low matches**, **Saved**, and **Ignored** tabs. By default, Recommended contains Strong or Good recommendations with Job Fit of at least 60 and High or Medium confidence. Possible matches contains qualifying Stretch opportunities. Low-match recommendations remain available in their own tab instead of distracting from the main list. Low-confidence and sub-threshold assessed jobs are hidden by default, with controls to broaden the minimum Job Fit, confidence, and recommendation tiers. Users can sort by recommended order, Job Fit, confidence, or newest posting. Recommended order uses recommendation tier first, then Job Fit, confidence, Preference Fit, and freshness. The initial HTML contains only summary scores and actions. Strengths, gaps, preference components, and provenance-bearing evidence references are loaded from a dedicated owner-scoped endpoint only when the user opens **View analysis**. The separate configuration view shows catalog management and scheduling only to authorized managers; every user can edit private search preferences and posting-age limits.

## Two-stage ranking and fit snapshots

Job Discovery does not send every collected posting to the AI model. The service runs two stages:

### Stage 1: inexpensive deterministic filtering

Every active posting is evaluated locally using the candidate's configured preferences and verified profile data:

- desired title terms and deterministic title overlap;
- preferred locations;
- accepted remote, hybrid, or onsite arrangements;
- employment type;
- advertised salary bounds when available;
- excluded terms anywhere in the posting;
- excluded job-title terms, which only match the normalized title;
- explicit work-authorization, sponsorship, citizenship, clearance, or license blockers; and
- deterministic overlap with verified skills.

A preference only hides a job when it is configured as mandatory, except both exclusion fields, an advertised salary maximum below the configured minimum, a rejected remote arrangement, and explicit eligibility contradictions. Posting-wide exclusions inspect the title, company, and description; title-only exclusions inspect only the job title. Hidden jobs remain stored as `DiscoveredJob` records but do not trigger an AI request. Jobs explicitly marked `ignored` are also stopped at Stage 1 on later refreshes, so ignoring a posting does not generate additional AI analysis. `DiscoveryResult.filtered_jobs` exposes the rejection reasons for the UI.

Stage 1 also calculates a separate **Preference Fit** score. It uses only configured search preferences:

- desired title overlap: 30%;
- preferred location: 25%;
- workplace type: 20%;
- employment type: 10%; and
- salary minimum, when compensation is published: 15%.

The score is normalized across only the configured and scoreable preference components. Missing salary is not treated as evidence against the candidate and is omitted from Preference Fit. Deterministic skill overlap is available as an inexpensive screening explanation, but it does not enter Preference Fit and never changes Job Fit.

### Stage 2: evidence-grounded assessment

Only jobs that pass Stage 1 continue. The default analyzer uses `ResumeAI` with `JOB_DISCOVERY_AI_MODEL`. When that variable is not set, Job Discovery uses `gpt-5-nano`, independently of the application's general `AI_MODEL_FAST` setting. Job Discovery explicitly defaults to `JOB_DISCOVERY_AI_REASONING_EFFORT=minimal` because requirement extraction is a latency-sensitive classification task, and it reserves a separate `JOB_DISCOVERY_AI_MAX_OUTPUT_TOKENS=4800` budget so internal reasoning cannot consume the entire structured-output allowance. These values remain configurable without changing the resume workflow's token limits.

1. `ResumeAI.analyze_job(description, title)` extracts structured requirements.
2. Each requirement is matched only to candidate-owned Career Profile records and candidate-confirmed or document-verified Evidence Library records.
3. Each requirement is classified as `supported`, `partial`, or `unsupported`. A match may be shown as a strength only when the fit snapshot contains the exact supporting record ID, type, label, field, and statement.
4. The shared `build_requirement_fit_assessment(...)` service calculates the **Job Fit** score, hard blockers, confidence, and recommendation.
5. The result is persisted as a `JobFitSnapshot`.

Before step 2, requirements that only describe search preferences—such as compensation, work location, remote/hybrid/onsite arrangement, relocation, or employment type—are excluded from the evidence-fit input. Those facts are handled by Stage 1 and Preference Fit instead. Eligibility requirements such as work authorization, citizenship, security clearance, and required professional licenses remain part of Job Fit and hard-blocker detection.

Job Discovery therefore does not maintain a second evidence-fit formula. The Resume Workflow and Job Discovery share the weighting, critical-requirement handling, hard-blocker detection, recommendation thresholds, confidence rules, and application-history calibration in `career_bridge/domain/fit_scoring.py`.


### Grounded result-card explanations

The discovery result card never renders `supported_requirements` directly as professional strengths. It renders only `JobFitSnapshot.evidence_matches`, and every match must contain at least one traceable record:

```text
RequirementEvidenceMatch
├── requirement_id
├── requirement
├── status: supported | partial
└── evidence[]
    ├── record_id
    ├── record_type
    ├── field_name
    ├── label
    ├── statement
    └── verification_status
```

Career Background skills, certifications, summaries, experiences, and education are linked to their actual Career Profile record IDs. Evidence Library statements are admitted only when candidate-confirmed or document-verified and retain their `EvidenceItem.id`. Unverified or rejected evidence is excluded before matching.

The matcher treats posting text and AI-extracted keywords only as the requirement side of the comparison. They can never become candidate evidence. Broad requirements are matched conservatively: one isolated keyword cannot fully support a multi-part requirement, and multiple traceable records may collectively provide coverage. If an older or incomplete fit snapshot has no provenance, the UI shows no strength and asks for reanalysis instead of inferring experience.

The card presents:

```text
Job title
Company · Location / Workplace

Job Fit
Recommendation
Confidence

Strongest matches — each linked to exact source records
Important gaps — explicitly marked not verified
Posting age

View posting · Save · Ignore · Create/Open Application Workspace
```

## Separate Job Fit and Search Priority

Ranked discovery results expose four explicit values:

```text
Job Fit:          professional evidence versus job requirements
Preference Fit:   desired title, location, workplace, employment type, and salary
Freshness:        how recently the posting was published or first discovered
Search Priority:  70% Job Fit + 20% Preference Fit + 10% Posting Freshness
```

For example, a result may display:

```text
Job Fit:          82
Preference Fit:   95
Freshness:        100
Search Priority:  86
```

`RankedJob` exposes these as `fit_score`, `preference_score`, `freshness_score`, and `search_priority`, together with the human-readable `priority_formula`. The legacy `RankedJob.score` property remains a compatibility alias for `fit_score`; ranked lists explicitly sort by `search_priority`. New UI and API code should use the explicit field names.

Posting freshness uses visible bands: within one day `100`, three days `95`, one week `85`, two weeks `70`, one month `50`, two months `25`, and older postings `10`. When no posting or first-seen date is available, freshness is neutral at `50`.

Search Priority is calculated at read/ranking time rather than stored in `JobFitSnapshot`, because posting freshness changes over time. This guarantees that posting age, location, salary, workplace type, employment type, and desired-title preferences cannot quietly alter the evidence-grounded Job Fit score.

### Fingerprint-based reuse

The expensive structured analysis is stored as a `JobAnalysisRecord` under:

```text
ANALYSIS#<job_id>#<description_fingerprint>
```

A profile change can reuse that analysis and rerun only deterministic evidence matching and fit scoring. `ResumeAI.analyze_job()` is called again only when the job's normalized description fingerprint changes or no cached analysis exists.

Fit results are stored under both evidence inputs:

```text
FIT#<job_id>#<profile_fingerprint>#<description_fingerprint>
```

An unchanged evidence profile and unchanged job description reuse the existing `JobFitSnapshot` without either an AI call or a scoring pass. A change to verified skills, evidence statements, verified clearances, or verified licenses/certifications creates a new fit snapshot while reusing the description analysis. A changed description creates a new analysis and fit snapshot without overwriting historical records.

Changing only search preferences does **not** invalidate the Job Fit snapshot. `CandidateJobProfile.evidence_fingerprint` is used for fit caching, while `preference_fingerprint` identifies the separate preference configuration. Location, salary, workplace, employment-type, and desired-title changes therefore recompute Preference Fit and Search Priority locally without rerunning the model or changing Job Fit.

`CandidateJobProfile.from_career_records(...)` converts the shared `CandidateProfile`, `CareerBackground`, and verified `EvidenceItem` records into discovery inputs. Evidence Library items must be candidate-confirmed or document-verified; unverified and rejected evidence cannot improve a discovery score. Optional mandatory search preferences and explicit eligibility facts can be supplied when constructing `CandidateJobProfile`.

## Result actions and explicit promotion

The dedicated Job Discovery page includes one card per active discovered result with these actions:

- **View posting** opens the canonical public source URL in a new tab.
- **Why this matches** explains the latest evidence-grounded recommendation and confirms that displayed strengths are linked to exact source records.
- **Show strengths and gaps** shows only provenance-bearing supported or partial matches as strengths and keeps unsupported or hard-blocker requirements visibly separate as unverified gaps.
- **Save** persists `DiscoveryJobState(disposition="saved")` without creating an application.
- **Ignore** persists `DiscoveryJobState(disposition="ignored")`; later discovery runs stop the posting before Stage 2 and make no AI call.
- **Create Application Workspace** explicitly promotes the selected posting into the existing application workflow.

Promotion uses the latest Job Fit snapshot for `alignment_score`; Search Priority and preference/freshness values are never copied into application evidence fit. The application is created with `status="considering"` and `workflow_step="setup"`, and receives a dedicated `source_job_id` equal to the deterministic discovered-job ID.

Duplicate protection is owner-scoped and race-safe:

- DynamoDB stores a conditional `SOURCE_JOB#<discovered_job_id>` link in the application table before writing the application item.
- Repeated or overlapping conversion requests resolve to the existing workspace and update the discovery state with its `application_id`.

The application-table item layout therefore includes the optional link:

```text
SOURCE_JOB#<discovered_job_id>
```

Deleting the linked application removes this duplicate-prevention link. It does not delete the historical discovered posting or its fit snapshots.

## Refresh and scheduling

### Administrator refresh of the shared catalog

The dedicated Job Discovery page provides **Refresh jobs for everyone** only to
administrators and configured Job Catalog Managers. The browser submits one
CSRF-protected request per enabled company to
`/applications/discovery/refresh/source` and displays live progress. It does not
hold one gateway request open while dozens of external career sites are scanned.
Each Company Sources card can call that same endpoint through **Scan this source**,
while the legacy `/applications/discovery/refresh` form route remains as a
no-JavaScript fallback and intentionally refreshes only one company per submission.

The progressive refresh:

1. verifies the actor has catalog-management permission for every request;
2. loads one enabled `CompanySource` owned by the built-in shared catalog;
3. reuses a fresh shared catalog or fetches and normalizes that company's public postings;
4. synchronizes the shared public catalog without using any individual user's profile;
5. publishes the collected postings for every user to browse;
6. continues to the next company even when one source reports an issue; and
7. preserves each user's private filters, saved/ignored state, fit snapshots, and Application Workspaces.

Interactive Workday requests retain their listing/detail/deadline limits. Manual
JSON-LD requests are temporarily limited to three pages, four-second requests,
and short request spacing. These temporary limits do not change the saved source
configuration, and the external scheduler can still perform complete scans.
The progress panel supports stopping after the current company. Restarting is
safe because recently refreshed sources are reused from the shared catalog.

The page shows the number of centrally enabled sources and the latest successful
catalog check time. If no source is enabled, the action returns a visible warning
and performs no network or AI work. Ordinary users cannot invoke source CRUD,
scheduling, or refresh routes even by sending those requests directly.

### User-specific assessment of collected postings

Collection and assessment are intentionally separate. Every signed-in user can
choose **Assess pending jobs for me** beneath the shared-catalog refresh action.
The browser calls `/applications/discovery/assess/pending` repeatedly in bounded
batches, without fetching any employer site. Each request scores only postings
that are still eligible, active, visible under mandatory preferences, and missing
a fit snapshot for the current Career Profile fingerprint.

One click assesses up to 25 postings by default, then reloads the page so the user
can immediately see **Recommended**, **Possible matches**, and **Low matches**.
The limit can be changed with
`CAREER_BRIDGE_DISCOVERY_ASSESSMENT_RUN_LIMIT` (maximum 100). Individual HTTP
requests process exactly one posting. This keeps each AI-backed response below
the web gateway timeout while the browser still continues through up to 25 jobs.
`JOB_DISCOVERY_AI_TIMEOUT_SECONDS` defaults to 20 seconds and is bounded between
5 and 25 seconds; Job Discovery uses one provider attempt per posting. The browser
automatically retries transient HTTP 502, 503, and 504 gateway responses twice.
Progress is reconciled from the durable pending queue after each response, so a
lost response cannot cause the requested run limit to be exceeded. A slow or
failing posting is isolated, reported in the progress panel, skipped for the rest
of the current run, and can be retried later without losing completed assessments.
If the gateway remains unavailable after retries, the run is shown as paused and
all completed assessments remain preserved.

Fit analysis remains user-specific. Opening **View analysis** can still assess one
already-materialized posting on demand against that user's verified Career Profile
and Evidence Library without fetching the company source again. Unchanged analysis
and fit snapshots are reused.

No scheduler, worker thread, or timer runs inside Flask or Gunicorn.

### Scheduled scans must run outside the web workers

`job_discovery.scheduling` is a process-independent entry point. It can be
invoked by:

- an AWS EventBridge schedule invoking Lambda with handler
  `job_discovery.scheduling.lambda_handler`;
- a scheduled container task running `python -m job_discovery.scheduling --scheduled`; or
- a controlled system cron process running the same command.

Example scheduled container or cron command:

```bash
export CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb
export CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=career-bridge-job-discovery
export AWS_REGION=us-west-2
# JOB_DISCOVERY_OWNER_IDS is optional. When omitted, the runner scans the
# built-in shared catalog owner.
python -m job_discovery.scheduling --scheduled
```

Example EventBridge input for Lambda:

```json
{
  "owner_ids": ["__SHARED_JOB_CATALOG_SOURCES__"]
}
```

Lambda respects the shared catalog schedule by default. A controlled administrative run can pass `{"force": true}` or omit `--scheduled` from the CLI to scan immediately. The external process should be invoked at least hourly when daily or weekly catalog scanning is enabled. `JOB_DISCOVERY_OWNER_IDS` remains supported for migration or multi-catalog deployments, but the default is the built-in shared catalog owner.

The external runner remains owner-scoped internally because the table has
no cross-owner scheduling index. For each explicit owner, scheduled mode reads
`PREFERENCES#SCHEDULE`, skips manual-only or not-yet-due owners, and records
`last_run_at` after a due scan. It never performs an unbounded table scan.
A production integration can supply a durable `CandidateProfileProvider` that
loads Career Profile and Evidence Library records for each owner. Without a
profile provider, the external process performs collection and synchronization
only; it does not call the AI model or create a Job Fit snapshot. The returned
summary makes this visible through `profile_available`.

For local controlled testing, the external command also supports
`CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=json` and
`CAREER_BRIDGE_JOB_DISCOVERY_JSON_PATH`. It rejects in-memory storage because a
short-lived scheduled process would discard all results when it exits.

Keeping scheduling external prevents duplicate scans across Gunicorn workers,
missed schedules during web redeployment, and coupling scan ownership to one
container instance.


## Page-load timing diagnostics

Every `GET /applications/job-discovery` response includes a `Server-Timing`
header so the browser network panel can show where server time was spent. The
metrics are prefixed with `jd_` and cover request context, workflow and reusable
profile loading, company-source and preference reads, candidate-profile
construction, materialized result-index access, template rendering, workflow
persistence, and the complete request total.

The server also writes one structured timing log per page request. It contains
the request reference, results/settings view, HTTP status, a hashed owner scope,
result-index state, total duration, and each phase duration. Requests at or above
`CAREER_BRIDGE_JOB_DISCOVERY_SLOW_REQUEST_MS` are logged at warning level; the
default threshold is `1000` milliseconds. Faster requests are logged at info
level. No user email address or raw owner identifier is written to this timing
log.

Example response header:

```text
Server-Timing: jd_context;dur=1.25;desc="Request context", jd_result_index;dur=8.40;desc="Result index read", jd_total;dur=24.73;desc="Job Discovery total"
```

## Required discovery test coverage

The discovery suite includes explicit tests for:

- one shared `JobSource` connector contract across Greenhouse, Lever, Ashby, Workday, SAP SuccessFactors, Oracle Cloud HCM, iCIMS, and fixture-based generic JSON-LD pages;
- normalization across the different public source formats;
- deduplication by source identity, canonical URL, and content fingerprint;
- changed-description reanalysis and unchanged-description/profile cache reuse;
- hard eligibility blockers before AI ranking;
- evidence provenance that prevents unsupported experience from becoming a displayed strength;
- owner isolation for source and job records;
- idempotent conversion into exactly one Application Workspace;
- persistence across separate repository instances and a second Flask application instance;
- DynamoDB round trips and stale-source optimistic-write rejection; and
- per-source failure isolation, including unavailable and robots-blocked company sites.

Run the focused set with:

```bash
python -m unittest -v \
  tests.unit.test_job_discovery_connector_contract \
  tests.regression.test_job_discovery_required_behaviors \
  tests.contracts.test_job_discovery_dynamodb \
  tests.unit.test_job_discovery_application_conversion \
  tests.integration.test_job_discovery_flask_persistence
```


### SAP SuccessFactors source

Choose **SAP SuccessFactors** and paste the employer's public career-site URL. The ATS site identifier is optional. The connector supports SAP-hosted Career Site Builder domains such as `*.jobs.hr.cloud.sap`, older `jobs2web.com` sites, and employer-owned custom domains. It normalizes root and `/search/` URLs to one shared-catalog identity, follows bounded public search pagination, extracts schema.org `JobPosting` data from detail pages, and defers excess detail requests during interactive refreshes to remain below the gateway timeout. No tenant credentials or authenticated SuccessFactors APIs are used.

### Oracle Cloud HCM source

Choose **Oracle Cloud HCM** and paste the employer's public Candidate Experience jobs page, for example `https://careers.oracle.com/en/sites/jobsearch/jobs` or `https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs`. The ATS site identifier is not used. The URL is normalized to the site's jobs collection so a jobs URL and a job-detail URL share one public-catalog identity. The connector first requests the bounded, unauthenticated Candidate Experience requisition collection and detail resources used by the career site. Oracle labels those CE resources as internal-use interfaces, so the connector treats them as a best-effort public-site contract rather than a supported customer integration. It sends the configured language, keeps all requests on the exact configured hostname, observes robots policy, and limits pages, records, detail requests, response sizes, redirects, request rate, and browser refresh time. Older deployments or vanity domains that do not expose those resources fall back to public HTML, embedded Oracle job records, and schema.org `JobPosting` data.


### iCIMS source

Choose **iCIMS** and paste the employer's public iCIMS jobs page, for example `https://careers-company.icims.com/jobs/search`. The ATS site identifier is not used. Classic portals and newer branded paths are normalized to a stable listing URL, and equivalent listing/job URLs share the public catalog when the listing path can be derived. The connector observes robots policy, stays on the exact configured `icims.com` hostname, follows bounded pagination, and extracts public listing-card fields plus schema.org `JobPosting` detail data. Interactive refreshes use the same temporary page, job, detail-request, timeout, and overall fetch-budget limits as the other URL-driven connectors.


### SmartRecruiters source

Choose **SmartRecruiters** and paste a public company career page such as `https://careers.smartrecruiters.com/ServiceNow`. The connector derives the company identifier and uses SmartRecruiters' unauthenticated public postings endpoints for bounded listing and detail retrieval. It maps public descriptions, qualifications, locations, employment type, department, dates, compensation when exposed, and apply URLs. No customer token or candidate API is used.

### Avature source

Choose **Avature** and paste a public hosted career-site URL such as `https://careers.avature.net/en_US/main/SearchJobs`. The connector normalizes dashboard, search, feed, and job-detail URLs to one site identity, reads the public search feed with bounded offsets, and enriches jobs from same-host public detail pages and schema.org `JobPosting` data. It does not use tenant credentials.

### Eightfold source

Choose **Eightfold** and paste either the standard public career URL, commonly `https://app.eightfold.ai/careers?domain=<company-domain>`, or an employer-owned Eightfold vanity URL such as `https://careers.costco.com/jobs`. Standard URLs preserve the public domain selector. Vanity job, category, location, and localized URLs normalize to the same employer-owned `/jobs` or `/careers` catalog, and every request remains restricted to that exact hostname. The connector parses public embedded position data and job links, follows bounded pagination, and enriches same-host public job pages. Eightfold's authenticated platform API is not used.

### Taleo source

Choose **Taleo** and paste a public Oracle Taleo Enterprise career-section URL containing `/careersection/<section>/`, such as `https://company.taleo.net/careersection/external/jobsearch.ftl`. Direct `jobdetail.ftl` URLs are normalized to the career section's search page. Discovery stays on the configured Taleo hostname, honors robots policy, and extracts public job links plus structured detail data.

### Dayforce source

Choose **Dayforce** and paste a public Dayforce Job Board URL such as `https://jobs.dayforcehcm.com/en-US/company/CAREERS`. The connector parses the public career portal and embedded structured job data with bounded pagination and detail retrieval. It does not use the authenticated Dayforce `JobFeeds` web service or require customer credentials.

### Talemetry / TTC Portals source

Choose **Talemetry / TTC Portals** and paste a public TalentTech/Talemetry Career Sites URL on `*.ttcportals.com`, such as `https://companycareers.ttcportals.com/search/jobs`. Root URLs, filtered search URLs, `/jobs/search`, and individual `/jobs/<job-id>-<slug>` links are normalized to the company's public jobs listing.

The connector reads the platform's paged public JSON listing at `/search/jobs.json?page=<n>` using an `application/json` request and the tenant's `/jobs` page as the referrer. It uses `talemetry_job_id` as the durable external ID, stores title and location directly from the listing envelope, and enriches a bounded number of jobs from the canonical detail pages. TTC defaults to one request per second, at most 50 listing pages, and ten detail-page enrichments per refresh unless the source explicitly overrides those limits. Remaining postings are still stored and can receive full descriptions through the existing on-demand detail lookup.

Some TTC edge configurations return HTTP 403 to server-side listing and detail requests. When that happens, the connector does not retry with browser impersonation or ignore the response. For **First Tech Federal Credit Union**, Career Bridge first reads the fixed, allow-listed employer page on the Partners in Diversity Career Center. That page publishes First Tech's current job links, posting metadata, and full descriptions as ordinary public HTML, so the normal refresh path no longer depends on OpenAI hosted search. The saved company source remains the official `firsttechfedcareers.ttcportals.com` Talemetry/TTC source; no deletion or recreation is required. Syndicated records are tagged `verified_employer_syndication`, constrained to `jobs.partnersindiversity.org`, and treated as a **partial scan** so a short publication delay cannot deactivate previously collected jobs. The fallback is enabled by default and can be disabled globally with `JOB_DISCOVERY_VERIFIED_SYNDICATION_FALLBACK=false` or per source with `verified_syndication_fallback=false`.

For other blocked TTC tenants, the domain-restricted hosted search fallback still accepts only exact official `/jobs/<numeric-id>-<slug>` URLs on the **same configured TTC hostname** and stores compact indexed metadata for confirmed open postings: title, location, posting date, and a factual role/requirements summary. It does not immediately reopen blocked detail URLs. A deterministic title derived from the official URL slug is used only when the hosted index omits a title. Indexed discovery is also marked as a partial scan. It can be disabled globally with `JOB_DISCOVERY_INDEXED_SEARCH_FALLBACK=false` or per source with `indexed_search_fallback=false`.

### UKG Pro / UltiPro source

Choose **UKG Pro / UltiPro** and paste the public JobBoard URL, such as `https://recruiting2.ultipro.com/WAS1000WTB/JobBoard/cb002c76-8419-4941-9c78-d28ae4e9c89e`. The connector extracts the tenant code and board UUID, calls the public `JobBoardView/LoadSearchResults` endpoint with bounded `Top`/`Skip` pagination, and enriches selected records from the public `OpportunityDetail?opportunityId=...` page. It supports `recruiting.ultipro.com`, numbered recruiting hosts such as `recruiting2.ultipro.com`, and equivalent `.ultipro.ca` recruiting hosts.

The integration uses only public candidate-facing pages, requires no UKG customer credentials, respects `robots.txt`, and applies the same hostname, redirect, timeout, response-size, rate-limit, and browser refresh limits as other URL-driven connectors. If a board blocks or changes the listing request, the source scan records the exact issue message and may fall back to public HTML or JSON-LD exposed by that same board.

### PeopleAdmin source

Choose **PeopleAdmin** and paste a public board URL such as `https://unc.peopleadmin.com/postings/search`, an individual posting such as `https://unc.peopleadmin.com/postings/123456`, or an institution-branded board such as `https://jobs.hrc.pdx.edu/`. The connector normalizes these forms to `/postings/search`, follows bounded `page=` pagination, extracts public posting links under `/postings/<numeric-id>`, and enriches them from the public detail page.

Choose **Radancy / TalentBrew** and paste an employer-branded public career URL such as `https://jobs.boeing.com/search-jobs`, a filtered location/category page, or an individual job URL such as `https://example.com/job/portland/data-engineer/123/987654321`. The connector normalizes the URL to the same-host `/search-jobs` page (preserving a locale prefix such as `/en/`), follows bounded pagination, and enriches each posting from its public detail page.

### Amazon Jobs source

Choose **Amazon Jobs** and paste an official URL such as `https://www.amazon.jobs/en/search?country=USA` or an individual posting under `https://www.amazon.jobs/en/jobs/<job-id>/<slug>`. The connector canonicalizes these forms to the official locale search page, retains public filters such as country or region, pages through recent public results with bounded offsets, and retrieves full descriptions from the corresponding official job pages. This is a company-specific connector for Amazon’s corporate career catalog; it does not cover internal employee postings or the separate `hiring.amazon.com` hourly workflow.

PeopleAdmin is commonly used by higher-education institutions, but the connector is intentionally limited to candidate-facing pages. When `robots.txt` blocks only the configured listing route, the connector can use OpenAI's hosted web-search index to discover official detail URLs on the **exact configured career-site hostname**. The application does not directly request the blocked `/postings/search` route. Instead, the first hosted-search strategy reads the search provider's indexed copy of that exact official listing page; a second compact `site:<host>` query runs only when the indexed listing is thin or temporarily unavailable. Career Bridge accepts only `/postings/<numeric-id>` URLs and still performs the normal robots check before reading each detail page. Indexed discovery is explicitly marked as a **partial scan**, so previously collected jobs are preserved instead of being deactivated merely because a search index did not return every posting. If the index is unavailable, returns no official URLs, or the detail pages are also blocked, Discovery retains cached jobs and reports the bounded fallback issue.

The fallback is enabled by default when `OPENAI_API_KEY` is present. Set `JOB_DISCOVERY_INDEXED_SEARCH_FALLBACK=false` to disable it globally, or set the source filter `indexed_search_fallback=false` for a specific board. `JOB_DISCOVERY_WEB_SEARCH_MODEL` defaults to `gpt-5-mini`. `JOB_DISCOVERY_WEB_SEARCH_TIMEOUT_SECONDS` is a total two-strategy budget that defaults to 50 seconds and is bounded to at least 45 seconds, so an older deployed value of 20 seconds no longer causes both hosted-search attempts to expire prematurely. `JOB_DISCOVERY_WEB_SEARCH_ATTEMPTS` defaults to two and is capped at two. `indexed_search_max_results` can cap results for a specific source. Each hosted-search request uses low search context, minimal reasoning, at most two tool calls for the direct indexed-listing strategy and one for the secondary site search. The connector stays on the exact configured hostname, ignores application and `pre_apply` routes, applies the standard redirect, response-size, request-rate, and interactive-refresh limits, and requires no PeopleAdmin customer credentials. Equivalent root, search, and detail URLs share one public-catalog identity.


### Branded Requisition Portal source

Choose **Branded Requisition Portal** for a public employer career site whose visible listing is available at `/search-jobs`, whose secondary public feed is available at `/api/requisitions/search`, and whose posting details use `/job/<id>/<slug>`. For Heritage Bank, use `https://careers.heritagebanknw.com/search-jobs`. The connector scans the visible HTML listing first, retries transient failures with bounded delays in the external runner, falls back to the public requisition endpoint, preserves the latest successful catalog on failure, and retrieves full public descriptions without accessing candidate accounts.
