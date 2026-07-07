# S3 request-cost tracking (GET/PUT attribution per table)

**Status:** design, not yet implemented. **Owner:** TBD. **Depends on:** an AWS source with S3
server access logging enabled on its buckets (see [§4](#4-discovery--onboarding-flow)) — though
onboarding a source with no prior logging setup is itself a first-class path, see §4.3.

## 1. Problem

Cryo already computes an accurate per-table **storage** cost — zero-scan, straight from Iceberg
manifest byte counts (`cryo/scanner/cost.py`, `cryo/cost/model.py`). It has no equivalent for
**request** cost (GET/PUT/LIST), which for high-churn or small-file-heavy tables can be a
meaningful share of a table's real S3 bill. AWS Cost Explorer and the Cost & Usage Report (CUR)
can't fill this gap either — S3 usage lines in CUR are broken down by bucket + usage-type, never
by object key or prefix, so there is no AWS-native way to ask "what did table X's GET/PUT
requests cost" without processing request-level data yourself.

The only field that currently exists in this space, `query_request_savings_usd_month`
(`cryo/cost/model.py`), is a **projected savings estimate** derived from internal query telemetry
(`delta_files × query_gets_per_file × query_count_30d × s3_get_usd_per_1k`) — not a measured
AWS request count. This doc adds the real thing alongside it.

## 2. Goals / non-goals

**Goals**
- Real, measured GET/PUT/LIST counts and $ cost per table, refreshed daily.
- **Minimize the gap between estimated and actual cost.** Request counts are measured, not
  estimated (§5) — but a measured count multiplied by a stale or wrong price is still a wrong
  answer. Pricing itself must be fetched live per account/region, not hardcoded — see §6.4.
- Cost of *running this feature* stays roughly flat regardless of fleet size or log history depth.
- Postgres footprint stays flat — one row per table, not one row per request or per day.
- Opt-in per bucket. Never silently enabled fleet-wide.
- **Zero org-specific assumptions.** Cryo is open source — this has to work for whoever adds a
  source next, on any AWS account, with no prior S3 logging setup and no hardcoded bucket names,
  account IDs, regions, or naming conventions anywhere in the implementation. Every bucket name,
  path, and target is either auto-discovered via an AWS API call or explicitly typed in by that
  org's own user. See [§4](#4-discovery--onboarding-flow).
- **Minimal config, auto-filled wherever AWS makes it possible.** A first-time adopter who has
  never touched S3 access logging before should be able to get from "added a source" to "seeing
  request costs" without reading an AWS doc page, if their credentials allow it — see the
  auto-provision path in §4.3.

**Non-goals**
- Real-time or near-real-time visibility. S3 server access logs are delivered best-effort,
  typically within a few hours, with no SLA and occasional duplicates or drops (see
  [§7](#7-latency--consistency)). This is an observability/cost-attribution feature, same
  philosophy as table health (computed from the last scan, not live) — not a live dashboard.
- Coverage for buckets where the user can't or won't grant `s3:GetBucketLogging` /
  `s3:GetObject` on the log target — those buckets fall back to "unknown," not an error state.
- S3-compatible-but-not-AWS storage (MinIO, Ceph, etc.) — this design is specifically AWS S3's
  `GetBucketLogging` API and access-log format. Cryo's own local dev stack runs on MinIO, which
  has a different audit-logging mechanism entirely; this feature should simply not appear (not
  error) for sources backed by non-AWS storage. Worth a follow-up doc if there's demand, not
  in scope here.

## 3. Options considered

| Approach | Per-table granularity | Cost to run | Scales to 1000s of tables? | Verdict |
|---|---|---|---|---|
| CUR / Cost Explorer | No — bucket + usage-type only | Already paying for it | N/A — can't do it at all | Ruled out, can't answer the question |
| CloudTrail S3 data events | Yes, per key | ~$1 / million events — expensive fast | No — cost scales linearly with request volume | Ruled out on cost |
| CloudWatch Request Metrics (per-prefix filter) | Yes, per prefix | $0.30/filter/month, capped at 1,000 filters/bucket | No — one filter per table hits the cap immediately | Ruled out on the filter cap |
| S3 Storage Lens (Advanced, prefix aggregation) | Yes, per prefix, "expanded prefixes" report | Priced per object monitored (~$0.20–0.30/million objects/mo) | Yes — built for billions of prefixes | Good AWS-native alternative if the team doesn't want to own log SQL |
| **S3 Server Access Logs + Trino** | Yes, per key → per table via prefix match | **Free to enable**; only log storage + query-engine scan cost, which we bound to near-zero (§5) | Yes | **Chosen** |

Access logs win primarily because they're free to turn on and Cryo already owns a Trino cluster
capable of doing the aggregation without new infrastructure. Storage Lens is the fallback worth
revisiting if a team would rather pay a small fixed AWS fee than manage the Trino table
definition below.

## 4. Discovery & onboarding flow

Everything in this section has to hold for an organization that has **never configured S3 access
logging before** and knows nothing about how Cryo happens to be deployed elsewhere — no assumed
bucket-naming convention, no assumed single shared "log bucket," no assumed account topology.
Every value comes from one of three places, always in the same priority order: **(1) an AWS API
response, (2) something the user typed for their own account, (3) a value Cryo offers to create
on the user's behalf with explicit confirmation.** Nothing is ever guessed or templated from
another org's setup.

### 4.1 Bucket discovery (fully automatic, always)

Cryo doesn't currently persist a table's S3 location — it's computed live at scan time
(`_table_location()` in `cryo/scanner/orphans.py:125`) and discarded after use except on failure
paths. That has to change first (see [§6.1](#61-tablelocation-column)).

Once `Table.location` is durable, per-source bucket discovery is just
`SELECT DISTINCT bucket FROM tables WHERE source_id = X` — always accurate, self-maintaining as
tables are added or moved, no separate catalog-discovery step to keep in sync. This step needs no
input from the user at all: it falls straight out of a normal scan, for any source, on any
account.

### 4.2 Logging-status check (automatic where permissions allow)

For each discovered bucket, on a schedule (or on-demand from the UI):

1. Call `s3:GetBucketLogging` using the source's existing AWS credentials
   (`AWSIntegration`/`AWSCostInsightConfig` access-key pattern — reuse it, don't invent a third).
2. **Success, logging enabled** → auto-fill `log_target_bucket` / `log_target_prefix` from the
   response. Zero manual input needed — this is the common case for any org that already has
   logging on, regardless of what they happened to name things.
3. **Success, logging disabled** → offer the one-click auto-provision path (§4.3), with manual
   setup (§4.4) as the fallback if they'd rather not grant the extra permission it needs.
4. **`AccessDenied`** (expected — read-scoped catalog credentials usually won't have this
   permission) → go straight to manual entry (§4.4), with the exact missing permission named so
   the user can grant it and retry auto-detect instead, if they prefer.

### 4.3 One-click auto-provision (new — the "minimal config" path)

For a bucket with logging disabled, if the credentials Cryo has also include
`s3:PutBucketLogging` (see [§11](#11-required-iam-permissions) — always optional, never assumed
present), the UI can offer to turn logging on for the user directly, with nothing to type:

1. Cryo proposes a target: an existing bucket the user already owns (picked from
   `s3:ListBuckets`, if granted) or a new bucket Cryo can create for this purpose
   (`<source-bucket>-cryo-access-logs`, only ever named from the *user's own* bucket name — never
   a fixed/shared name). S3 bucket names are unique **globally, across every AWS account in the
   partition — not just within a region or account** — so a name derived from the user's own
   already-unique bucket name is *extremely* unlikely to collide, but not literally impossible
   (someone else could already hold that exact string). Handle `BucketAlreadyExists`/
   `BucketAlreadyOwnedByYou` on creation by appending a short random suffix and retrying once,
   rather than failing the whole flow on a one-in-a-million naming collision.
2. The user reviews and confirms the exact target bucket/prefix before anything is created —
   this is a write action, so it's a confirm-then-act step, not silent.
3. Cryo calls `s3:PutBucketLogging` **and separately ensures the target bucket's policy grants
   log delivery — see §4.3.1, this is not automatic.**
4. Result is identical to the "already enabled" case in §4.2 step 2 — logging config is now
   auto-filled, ingestion (§5) can proceed once the first log file actually lands (§4.3.1's
   verification step, not immediately after the API calls return).

This is the step that makes the feature "minimal config" for a brand-new adopter with no prior S3
logging setup: **detect → confirm → done**, no AWS console visit required, no docs to read, and
still nothing hardcoded — every name is derived from that user's own bucket at click-time.

### 4.3.1 The part that actually makes logs flow (don't skip this)

`s3:PutBucketLogging` alone is **not sufficient** — this is the riskiest step in the whole design
and the one most likely to produce "successfully enabled, but no logs ever arrive, and the user
only finds out a day later when the dashboard is still empty." The API call configures the
*source* bucket to send logs; it does not automatically grant the *target* bucket's permission to
receive them.

- Every bucket created since April 2023 defaults to **Object Ownership: Bucket owner enforced**,
  which disables ACLs entirely. Under that (now-default) setting, log delivery can **only** be
  granted via a bucket policy statement on the target — `PutBucketLogging`'s legacy
  ACL-grantee option silently does nothing. So: never attempt an ACL-based grant; always manage
  this as a bucket-policy statement.
- The required statement grants `s3:PutObject` to the `logging.s3.amazonaws.com` service
  principal, scoped to the target prefix, with an `aws:SourceAccount` condition (and ideally
  `aws:SourceArn`) tying it to the specific source bucket — see [AWS's example policy](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html).
  Cryo must **read the target bucket's existing policy first (`s3:GetBucketPolicy`) and merge
  this statement in**, not overwrite — a target bucket picked from the user's existing buckets
  may already have unrelated policy statements, and blindly replacing the policy would break
  whatever those were protecting.
- **Verify, don't assume.** Because delivery is best-effort and can take hours (§7), a green
  checkmark right after the API calls return would be a lie. Show an honest intermediate state —
  "logging configured, waiting for first log delivery (usually within a few hours)" — and flip to
  "active" only once an ingestion run (§5) actually finds a log file under the target prefix, not
  the moment `PutBucketLogging`/`PutBucketPolicy` return `200`.

### 4.4 Manual entry (always available, always the fallback)

If auto-detect fails and the user doesn't want to grant `s3:PutBucketLogging` for auto-provision,
they can type the log target bucket/prefix themselves — this must remain a first-class path, not
a degraded one, since some orgs will always prefer to manage bucket logging outside Cryo (e.g. a
central security team owns it). The UI should validate the entry (a lightweight `s3:ListObjectsV2`
probe against the given target/prefix, reporting "found N log files" or "nothing here yet — check
the path") rather than accepting it blind.

## 5. Ingestion pipeline

The scale/cost constraint drives every choice here: **cost of measuring cost must stay flat**,
regardless of fleet size or how much log history has piled up in S3.

1. **Register the log target bucket as a Hive-partitioned external table in Trino**, one time per
   log-target-bucket (note: multiple source buckets often share one log-target-bucket, each under
   its own subfolder — register once, not once per source bucket). S3 access logs are already
   laid out as `.../<bucket>/<year>/<month>/<day>/...`, which maps directly onto Hive partition
   columns. This is what makes partition pruning possible in step 3.
2. **Checkpoint the last-ingested date per bucket.** Each run only touches new partitions.
3. **Re-aggregate a small trailing window (last 2–3 days), not just "yesterday."** Access log
   delivery is best-effort and can straggle in late (§7) — a strict "touch day D exactly once"
   design would silently under-count on any day with late arrivals. Re-scanning 2–3 partitioned
   days is still cheap (partition pruning means Trino never reads outside that window), and the
   aggregation is naturally idempotent (`COUNT(*)` over the same partition twice gives the same
   answer), so this also absorbs AWS's documented possibility of duplicate log records for free.
4. **One Trino query per bucket per run**: `GROUP BY table_prefix, operation, date` over just
   that trailing window, matching `key` to `table_id` by longest-prefix match against `Table
   .location`. Output is a few thousand rows per bucket per run, not millions of raw log lines.

   **This longest-prefix match is the algorithmic core of the whole feature, and the rest of this
   list is easy by comparison — it deserves more than one bullet.** A naive `WHERE key LIKE
   location || '%'` join against every known table forces, per log row, a scan of all N table
   locations for that bucket to find the longest match; at 1,000+ tables and millions of log rows
   per partition this either needs the right join strategy or it becomes the slow part of an
   otherwise cheap design. Two directions worth benchmarking before this ships, not after:
   broadcast the (small) table-location list to every worker via `CROSS JOIN` against a values
   list rather than a row-by-row subquery, so the CBO treats it as a broadcast join instead of a
   nested-loop; or precompute a location-prefix index (sorted, so a range/binary-search style
   lookup replaces "check every location") and expose it as a small lookup table or UDF rather
   than a raw string match per row. Whichever wins, **benchmark with realistic row counts (the
   fleet's actual table count × a busy bucket's actual daily log-row count) before shipping §5's
   P1 implementation** — this is not something to discover is slow after it's already the thing
   every ingestion run waits on.

   **Back-of-envelope, so this isn't just an abstract "keep it cheap" — a rough working number:**
   a 10 TB bucket doing ~50M requests/month is roughly 1.7M requests/day; each S3 access log line
   is ~250–300 bytes, so that's very roughly ~500 MB/day of raw log text for that one bucket. A
   single Trino query over a 3-day partition-pruned
   window at that scale should be a low-tens-of-seconds job on a modest cluster, not minutes —
   if a real benchmark comes back an order of magnitude slower than that, the join strategy above
   needs fixing before this ships, not after. Treat "single query, single bucket, single day,
   roughly 30s" as the sanity-check number to hold P1's actual benchmark against.
   **Cross-table prefix collision — do not simplify this away.** Iceberg tables in the same
   bucket routinely share a common parent prefix (`warehouse/db1/table_a/...` and
   `warehouse/db1/table_b/...` both sit under `warehouse/db1/`). A log entry for
   `warehouse/db1/table_b/2024-12-30/part-00000.parquet` must match `table_b`'s location, not
   `table_a`'s and not the shared `warehouse/db1/` parent as if it were its own table. The
   longest-prefix match against `Table.location` handles this correctly by construction — a
   future "simplification" that swaps it for a bare `STARTS_WITH()`/first-match check would
   silently misattribute every table's request cost to whichever sibling table happens to sort
   first, and the bug would be invisible in aggregate (total request count across the bucket
   stays right, only the per-table split is wrong) — exactly the kind of regression that survives
   code review. Keep the "longest match wins" requirement explicit in the implementation, not
   just implied by "prefix match."
5. **UPSERT the result into a small rolling table** (§6.2) — overwrite each day's row for that
   table rather than appending, so re-aggregating the trailing window never duplicates counts.
6. **Never pull raw log rows into Cryo's own process, and never store them in Postgres.**
   Aggregation happens entirely inside Trino; Cryo only ever sees the small grouped result.

This bounds Cryo's own AWS/Trino spend on this feature to "a few days of one bucket's logs, per
bucket, per day" — forever, independent of how many months of log history exist in S3 or how many
tables the fleet grows to.

## 6. Data model changes

### 6.1 `Table.location` column

New nullable `VARCHAR` column on `Table`, populated in `persist_table_analysis()`
(`cryo/scanner/persistence.py`) from the same value `_table_location()` already computes at scan
time — currently thrown away on the success path. Small, additive migration; not a scale concern
on its own (one string column, updated in place per table, not appended-to).

### 6.2 `S3BucketLogConfig` (new)

One row per `(source_id, bucket)`. Reuses the `access_key_id` / `secret_ref` credential shape
already used by `AWSIntegration`/`AWSCostInsightConfig` rather than introducing a third pattern.

**This is now three per-source, bucket-scoped AWS config tables** (`AWSIntegration`,
`AWSCostInsightConfig`, `S3BucketLogConfig`). Three of the same shape is a pattern, not a
coincidence, and worth naming explicitly rather than letting it grow silently: **if a fourth is
ever proposed, the right answer is to converge these into one `cryo.db.models.AWSSourceConfig`
with a `kind` discriminator, not add a fourth near-identical table.** This is the same
open-source-hygiene concern as a hardcoded bucket name, just at the schema level — an OSS
contributor grepping the models file for "how do I add per-source AWS config" should find one
converging pattern, not have to guess which of three (soon four) tables to extend.

| Field | Type | Notes |
|---|---|---|
| `source_id` | FK | |
| `bucket` | string | discovered via §4, not stored redundantly elsewhere |
| `logging_status` | enum | `enabled` / `disabled` / `unknown` |
| `log_target_bucket`, `log_target_prefix` | string, nullable | auto-filled or manually entered |
| `manual_override` | bool | true if the user typed the path themselves |
| `enabled` | bool | opt-in switch — ingestion only runs for buckets where this is true |
| `last_checked_at` | timestamp | last `GetBucketLogging` check |
| `last_ingested_date` | date | checkpoint for §5 step 2 |

### 6.3 `TableCost` additions, and the `S3RequestCostDaily` audit trail behind them

New nullable fields on the existing `TableCost` row (not a per-request or per-day history table
itself — one row per table, matching the "flat Postgres footprint" goal):

- `get_requests_30d`, `put_requests_30d`, `list_requests_30d` (integers)
- `request_cost_usd_month` (float) — computed cost, summed with the existing storage cost for a
  true all-in monthly estimate
- `request_cost_pricing_source` (`"live"` / `"fallback_default"`) — so the UI never shows a
  fallback-derived number with the same visual confidence as a live-fetched one (§6.4, §9.2)
- `NULL` across all four (not `0`) means "no ingestion has run for this table yet" — distinct
  from "measured zero requests." The UI must not render that as `$0`.

These are a **read-optimized rollup**, not the ingestion target itself. §5 step 5's UPSERT
writes into a separate `S3RequestCostDaily` table — one row per `(source_id, bucket, table_id,
day, operation_tier)`, unique-constrained on that full key so `ON CONFLICT` actually works, and
bounded by a retention job (default 35 days — a few days past the 30-day rollup window, so a
delayed rollup run never reads a just-pruned day) rather than kept forever. `TableCost`'s four
fields above are recomputed from a `SUM(...) WHERE day >= now() - 30d` over this table after
each ingestion run. This two-layer split is what keeps the UI-read path cheap (one row) while
still giving the ingestion job something it can safely upsert into and reconcile against (§5
step 3's trailing-window re-aggregation needs a per-day row to overwrite, not a single rolling
number it would otherwise have to read-modify-write against a moving 30-day sum every run).

### 6.4 Pricing: fetched live, not hardcoded

`cryo/config.py` currently has `s3_get_usd_per_1k` (0.0004) and an unused `s3_list_usd_per_1k`
(0.005) as **hardcoded defaults**. That's the wrong pattern to extend for PUT pricing, and worth
fixing for GET/LIST at the same time — a fixed constant is a US-Standard-tier snapshot from
whenever it was written, and S3 request pricing varies by **region** and by **storage class**
(Standard vs. Standard-IA vs. Glacier request pricing differs meaningfully), and AWS revises
prices periodically. A hardcoded default silently drifts from reality, which directly works
against the goal of keeping the estimate-vs-actual gap small — and it's also an org-specific
assumption in disguise (whichever region the constant was tuned for), which cuts against the
open-source goal in §2 just as much as a hardcoded bucket name would.

**Fetch it instead, the same way Cryo already fetches storage pricing.** `fetch_s3_price_per_gb()`
(`cryo/integrations/aws_s3_metrics.py:211`) already calls the AWS Price List API
(`pricing:GetProducts`) to get the real $/GB rate for the account's actual region and storage
class — extend that same call (or add a sibling function) to also pull the request-pricing SKUs
(AWS's Price List entries for `Requests-Tier1` ≈ PUT/COPY/POST/LIST and `Requests-Tier2` ≈ GET/
SELECT, keyed by region + storage class). One call, cached, covers GET/PUT/LIST/HEAD together —
no reason to fetch them separately.

- **Cache per (region, storage class)**, not per request — pricing changes rarely. A daily or
  weekly TTL is plenty; refetch on cache miss or expiry, not on every cost calculation.
- Note the AWS quirk: the Price List API is a **global, `us-east-1`-only endpoint** regardless of
  which region the bucket itself is in — the region is a query parameter, not the endpoint.
- **Fallback only if `pricing:GetProducts` isn't granted**: fall back to a hardcoded default, but
  — critically — **label any cost computed from the fallback as approximate in the UI** (e.g. a
  tooltip: "estimated using a default rate — grant `pricing:GetProducts` for this account's exact
  regional pricing"). Never present a fallback-derived number with the same visual confidence as
  a live-fetched one; that's exactly the estimate/actual gap this feature exists to close.
- **A second, distinct failure mode: permission granted, response empty.** AWS's Price List is
  documented as incomplete for some SKUs in some regions, and a `GetProducts` call can return
  `200` with zero matching price items rather than an error — this is not hypothetical, it's a
  known AWS quirk. Treat "granted but empty" the same as "not granted" for the fallback-label
  purpose above, but **also log a grep-able `WARNING`** naming the exact region/storage-class/SKU
  that came back empty. Without that log line, "we know this table's cost is approximate" quietly
  becomes "we shipped silent drift and won't notice until someone compares against their AWS
  bill" — the whole point of fetching live pricing is defeated if a silent-empty response looks
  identical to a successful fetch anywhere in the pipeline.
- This removes the `s3_get_usd_per_1k`/`s3_list_usd_per_1k` config constants as the source of
  truth (they become the fallback-only defaults) and adds no new hardcoded PUT default at all.

## 7. Latency & consistency

S3 server access logs are [documented by AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html)
as best-effort: typically delivered within a few hours, no SLA, occasionally much later or not at
all, and occasionally duplicated. Practical implications for this design:

- A daily batch that only ingests "yesterday" already has slack for the typical few-hour lag.
- The 2–3 day reconciliation window in §5 step 3 absorbs stragglers beyond that.
- This is fundamentally not suited to real-time alerting. If a future need for near-real-time
  GET/PUT visibility on a *specific* hot table comes up, that's CloudWatch Request Metrics scoped
  to that one table, not a fleet-wide mechanism — see the cap discussion in §3.

## 8. API surface (new)

Following the existing `cost_insights_routes.py` pattern (manual-trigger POST endpoints, not
cron-only):

- `GET /sources/{id}/s3-log-configs` — list discovered buckets + their logging status.
- `PATCH /sources/{id}/s3-log-configs/{bucket}` — set manual `log_target_bucket`/`prefix`,
  toggle `enabled`.
- `POST /sources/{id}/s3-log-configs/{bucket}/check-logging` — re-run the `GetBucketLogging`
  check on demand.
- `POST /sources/{id}/s3-log-configs/{bucket}/ingest` — manually trigger an ingestion run
  (same as what the scheduled job calls).
- `POST /sources/{id}/s3-log-configs/{bucket}/teardown` — the destructive cleanup path (§9.4),
  distinct from `PATCH ... {enabled: false}`: removes the config row and, only for policy/bucket
  state Cryo itself created in §4.3.1, offers to revert it.

## 9. Frontend changes

### 9.1 Source detail — per-bucket logging panel

New panel under the Source detail page (near the existing AWS cost/inventory settings): one card
per discovered bucket. The card's primary action changes based on §4's state machine, so the same
simple UI works for any org regardless of what state their AWS account is already in — this is
the concrete UI expression of the "zero org-specific assumptions" goal in §2, not a separate
concern from it:

| Bucket state (§4) | Card shows | Primary action |
|---|---|---|
| Logging enabled (auto-detected, §4.2 step 2) | "Tracking N tables", last-ingested date | Link into the cost breakdown — nothing to configure |
| Logging disabled, write access available (§4.3) | Proposed target bucket/prefix | **"Enable logging"** — confirm-then-act, per §4.3 step 2 |
| Logging disabled/undetectable, read-only access (§4.4) | Manual-entry form | Inline validation as the user types: "found 1,204 log files at this path" / "nothing found yet — double check the path" |
| Awaiting first log delivery (§4.3.1) | "Logging configured, waiting for first delivery (usually within a few hours)" | Nothing to click — this is a transient state, not a dead end |
| No source added yet | Panel doesn't render | Bucket discovery depends on §4.1, which depends on a completed scan |

### 9.2 Table Detail — cost-share breakdown

**The actual UI win here isn't a new line item, it's a ratio.** Just adding "Requests (30d): $X"
next to storage cost is correct but understates the value — an operator can already infer storage
cost roughly from table size, and doesn't need Cryo to tell them that number. What they can't
already infer is *how a table's request pattern skews its bill*: "GET/PUT is 23% of this table's
S3 cost, storage is only 60%" tells them "this is a high-churn table, small-file compaction
matters more here than raw size suggests" — that's the insight, not the raw dollar figure.
Surface it as a storage/request cost-share breakdown (a simple stacked bar or two labeled numbers
is enough, doesn't need a new chart type), and once it exists, the Data Quality / Table Explorer
"top offenders" sort should be able to sort by request-cost-share too, not just total cost —
that's likely where operators will actually find the tables this feature was built to surface.

### 9.3 States: loading, empty, stale, error

Every async step in §4/§5 needs an honest UI state, not just a happy path:

- **Loading** — the §4.2 `GetBucketLogging` check and the §4.4 manual-entry validation probe are
  both live AWS calls; show a spinner on that one bucket's card, not a full-page block, since
  other buckets' checks may already be done.
- **Empty** — zero buckets discovered (no source, or a source with no completed scan yet): don't
  show an empty per-bucket panel with no explanation; point back at "run a scan first."
- **Stale** — an ingestion run (§5) that hasn't completed in longer than its normal cadence
  (missed its daily run, a Trino query failing repeatedly) should surface as "last successful
  ingestion: 3 days ago" on the bucket's card, not silently keep showing old numbers as if
  current. The Table Detail cost-share breakdown (§9.2) should carry the same staleness signal —
  a number that's quietly a week old and displayed identically to a fresh one is worse than no
  number, given this whole feature exists to close an accuracy gap.
- **Error** — a failed `PutBucketPolicy` merge (§4.3.1), a Trino query failure, or a
  `pricing:GetProducts` empty-response (§6.4) should each surface as a specific, actionable
  message tied to that bucket/table — never a generic "something went wrong" toast that gives the
  user no next step.

### 9.4 Rollback / teardown

Turning a bucket **off** needs to be as considered as turning it on — this was missing from the
original design and shouldn't be an afterthought bolted on after P2 ships:

- Setting `S3BucketLogConfig.enabled = false` (via the existing `PATCH` endpoint, §8) stops new
  ingestion runs immediately but **leaves everything else in place** — the config row, the S3
  bucket logging configuration itself, and any target-bucket policy Cryo added. Re-enabling later
  is then just flipping the flag back, with no need to re-run §4.3 or re-grant anything.
- A separate, explicit **`POST /sources/{id}/s3-log-configs/{bucket}/teardown`** action (not the
  same button as "disable") is for someone who wants Cryo to actually clean up after itself: it
  removes the `S3BucketLogConfig` row, drops the Trino external table registration for that
  bucket if no other enabled bucket still shares it, and — only if Cryo created the target bucket
  or added the policy statement itself in §4.3.1 — offers to revert that policy statement
  specifically (never touch statements Cryo didn't add) or leave logging running on the AWS side
  if the user would rather keep it for their own purposes outside Cryo.
- Teardown is a destructive, hard-to-fully-reverse action (a removed bucket-policy statement isn't
  automatically re-creatable from Cryo's side once gone) — gate it behind an explicit confirm
  step that names exactly what will and won't be touched, matching how the rest of Cryo treats
  irreversible actions elsewhere in the app.

### 9.5 Plain-language copy

No step in this flow requires the user to already know AWS S3 access-logging concepts going in —
the copy on each state should explain what's happening in plain terms ("Cryo can see request
activity for this bucket" / "turn this on to see request costs, not just storage costs"), since
this is aimed at any OSS adopter, not just teams that already run FinOps tooling.

## 10. Scheduling

Opt-in per bucket, matching how CUR/inventory ingestion works today (manually triggered via POST,
no cron yet). A `DaemonRunner` subclass (same pattern as `AlertDispatcherRunner` /
`BenefitScoreUpdaterRunner` in `cryo/cli.py`) is the natural fast-follow once the manual path has
been validated against a handful of real buckets — not part of the first cut.

Plan capacity around: **one query per enabled source bucket per day**, each expected to land in
the low tens of seconds per §5's back-of-envelope estimate, against a log target that grows by
roughly single-digit GB/day per busy bucket. That's the number to hold a real `DaemonRunner`
rollout's cluster capacity against — if actual measured runs come in meaningfully above that once
P1 ships, treat it as a signal the §5 join strategy needs revisiting before scheduling more
buckets onto it, not as a reason to just widen the cron interval.

## 11. Required IAM permissions

Document these explicitly in the UI (a copy-pasteable IAM policy snippet, generated with that
org's actual bucket names filled in — never a generic example the user has to hand-edit), since
the credentials a user hands Cryo for catalog access usually won't include them by default.
Split into a required tier and an optional convenience tier, so an org can pick exactly how much
access they're comfortable granting:

**Required — read-only, needed for §4.1/§4.2 detection and §5 ingestion:**
- `s3:GetBucketLogging` on each source bucket.
- `s3:GetObject`, `s3:ListBucket` on the **log target** bucket/prefix specifically — not on the
  source buckets themselves, since Cryo never needs to read the data, only the logs about it.
- `pricing:GetProducts` (account-level, not bucket-scoped — see §6.4) for live, region-accurate
  GET/PUT/LIST rates. Without it, cost figures still compute but are visibly labeled as
  approximate (§6.4) rather than silently wrong — this permission is what closes that gap, so
  it belongs in the required tier even though nothing breaks without it.

**Optional — only needed for the §4.3 one-click auto-provision convenience path; the feature is
fully usable without these, via manual entry (§4.4) instead:**
- `s3:PutBucketLogging` on the source bucket.
- `s3:GetBucketPolicy`, `s3:PutBucketPolicy` on the target bucket — needed **every time**, not
  only for a newly-created target (§4.3.1): even an existing bucket the user already owns needs
  its policy read (to merge into) and updated to grant `logging.s3.amazonaws.com` write access,
  since that grant essentially never already exists on a bucket that wasn't already a log target.
- `s3:CreateBucket` — only if the user chooses "create a new bucket for logs" rather than
  pointing at one they already own.

An org that only wants the required tier should see the exact same feature, minus the one-click
button — never a degraded or partially-broken experience.

## 12. Rollout phases

- **P0a — extract live pricing, ship it alone, first.** Just `fetch_s3_request_prices()` (or
  whatever it ends up named): the Price List API call for GET/PUT/LIST rates, the
  per-(region, storage class) cache, and the empty-response `WARNING` log (§6.4) — nothing else
  from this doc. Then re-point the *existing* `query_request_savings_usd_month`
  (`cryo/cost/model.py`, §1) at this live rate instead of the hardcoded `s3_get_usd_per_1k`. This
  is deliberately not bundled with P0 below: that existing estimate already feeds the Governor
  and the cost dashboard *today*, for every current user, and is already wrong in the same way
  this whole doc exists to fix for the new feature. A self-contained, one-day PR here starts
  closing the estimate/actual gap immediately, independent of whether or when the rest of this
  design ships. Getting this ordering right matters more than the other phases — it's the one
  case where shipping the pieces in the wrong order (new feature first, existing-estimate fix as
  an afterthought) is hard to walk back later, since P0 below would otherwise build its own
  one-off pricing plumbing that P0a would then have to retrofit everyone else onto.
  **Implemented**: while wiring this, the actual scan call sites (`cryo/scanner/run.py`'s
  `_rebuild_cost_analysis`) turned out to hardcode `query_count_30d=0` unconditionally — meaning
  `query_request_savings_usd_month` computed to exactly `$0` for every table in production
  regardless of price, a bigger and entirely separate pre-existing bug from the pricing gap this
  phase set out to fix. Fixed alongside it: the previous scan's `TableStats.query_count_30d` is
  now threaded through as the input (same staleness tradeoff already accepted for orphan bytes
  via `carry_forward_orphans`), so P0a's "closes the gap immediately" claim is actually true
  rather than closing a pricing gap that a separate zeroed-out multiplier was masking entirely.
- **P0** — `Table.location` migration; `S3BucketLogConfig` model + migration; `GetBucketLogging`
  discovery + manual-entry UI (§4.1, §4.2, §4.4), consuming `fetch_s3_request_prices()` from P0a
  rather than building its own pricing call. This alone is a complete, usable feature for any org
  with the required (read-only) permission tier.
- **P1** — Trino external table registration + the partition-pruned, reconciling ingestion query
  (§5, including the longest-prefix-match benchmarking and the cross-table-prefix guard called
  out there); manual-trigger API endpoints; `TableCost` request-cost fields wired into the
  existing cost breakdown UI as a cost-share ratio, not just a raw number (§9).
- **P2** — One-click auto-provision (§4.3/§4.3.1) — the minimal-config convenience path for orgs
  that grant the optional write-permission tier, including the target-bucket-policy verification
  step and the teardown flow (§9); `DaemonRunner`-based scheduling once P1 has been validated
  against real buckets; Storage Lens as an alternate ingestion path for teams who'd rather not
  manage the Trino table.

## 13. Open questions

- What the fallback default rate should be when `pricing:GetProducts` isn't granted — needs to be
  clearly stale-dated in the code (a comment noting when it was last checked against the AWS
  pricing page) since, unlike the live-fetched path, nothing will catch it drifting out of date.
- Cache TTL for fetched pricing (§6.4) — daily vs. weekly is a reasonable range; not yet decided.
- Whether `S3BucketLogConfig` credentials should be allowed to differ from the source's own
  catalog credentials (e.g. a narrower role that only has the §11 permissions) — likely yes, but
  not yet decided.
- Whether Storage Lens (§3 runner-up) should be built as a parallel ingestion path from day one,
  or only added later if a team specifically asks for it.
- **Fallback-vs-hard-failure when pricing is grantable but genuinely absent.** §6.4's "granted
  but empty" case assumed the fix is always "fall back and label approximate" — but there's a
  documented AWS inconsistency where a region that visibly exists and bills normally in the AWS
  console (historically true for some edge-case regions) still returns zero Price List results
  for a given SKU. Is "fall back, label approximate" still the right call in that case, or should
  a *region that should have real pricing but doesn't* refuse to compute a cost at all — an empty
  cost section on the affected table rather than a labeled-but-plausible-looking number? Currently
  this doc says "fall back" uniformly; worth confirming that's actually the right trade-off before
  P0a ships, not after.
