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
   a fixed/shared name, since two orgs' buckets can't collide on a name that's derived from their
   own resource).
2. The user reviews and confirms the exact target bucket/prefix before anything is created —
   this is a write action, so it's a confirm-then-act step, not silent.
3. Cryo calls `s3:PutBucketLogging` (creating the target bucket and its log-delivery bucket
   policy first, if the user picked "new bucket").
4. Result is identical to the "already enabled" case in §4.2 step 2 — logging config is now
   auto-filled, ingestion (§5) can proceed.

This is the step that makes the feature "minimal config" for a brand-new adopter with no prior S3
logging setup: **detect → confirm → done**, no AWS console visit required, no docs to read, and
still nothing hardcoded — every name is derived from that user's own bucket at click-time.

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

### 6.3 `TableCost` additions

New nullable fields on the existing `TableCost` row (not a new history table — one row per table,
overwritten daily, matching the "flat Postgres footprint" goal):

- `get_requests_30d`, `put_requests_30d`, `list_requests_30d` (integers)
- `request_cost_usd_month` (float) — computed cost, summed with the existing storage cost for a
  true all-in monthly estimate

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

## 9. Frontend changes

- New panel under the Source detail page (near the existing AWS cost/inventory settings):
  per-bucket cards, one per discovered bucket, each showing logging status and target path.
  The card's primary action changes based on §4's state machine, so the same simple UI works for
  any org regardless of what state their AWS account is already in:
  - Logging already enabled → nothing to do, shows "tracking N tables" and a link into the cost
    breakdown.
  - Logging disabled, `PutBucketLogging` available → a single **"Enable logging"** button that
    runs §4.3, with the proposed target bucket/prefix shown before the user confirms.
  - Logging disabled or undetectable, no write permission → the manual-entry form (§4.4) with
    inline validation feedback ("found 1,204 log files at this path" / "nothing found yet").
  - No source added yet → this whole panel doesn't apply; it only appears once a source with
    scanned tables exists, since bucket discovery depends on §4.1.
- Table Detail → Cost tab: **the actual UI win here isn't a new line item, it's a ratio.** Just
  adding "Requests (30d): $X" next to storage cost is correct but understates the value — an
  operator can already infer storage cost roughly from table size, and doesn't need Cryo to tell
  them that number. What they can't already infer is *how a table's request pattern skews its
  bill*: "GET/PUT is 23% of this table's S3 cost, storage is only 60%" tells them "this is a
  high-churn table, small-file compaction matters more here than raw size suggests" — that's the
  insight, not the raw dollar figure. Surface it as a storage/request cost-share breakdown (a
  simple stacked bar or two labeled numbers is enough, doesn't need a new chart type), and once
  it exists, the Data Quality / Table Explorer "top offenders" sort should be able to sort by
  request-cost-share too, not just total cost — that's likely where operators will actually find
  the tables this feature was built to surface.
- No step in this flow requires the user to already know AWS S3 access-logging concepts going in
  — the copy on each state should explain what's happening in plain terms ("Cryo can see request
  activity for this bucket" / "turn this on to see request costs, not just storage costs"), since
  this is aimed at any OSS adopter, not just teams that already run FinOps tooling.

## 10. Scheduling

Opt-in per bucket, matching how CUR/inventory ingestion works today (manually triggered via POST,
no cron yet). A `DaemonRunner` subclass (same pattern as `AlertDispatcherRunner` /
`BenefitScoreUpdaterRunner` in `cryo/cli.py`) is the natural fast-follow once the manual path has
been validated against a handful of real buckets — not part of the first cut.

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
- `s3:CreateBucket`, `s3:PutBucketPolicy` — only if the user chooses "create a new bucket for
  logs" rather than pointing at one they already own.

An org that only wants the required tier should see the exact same feature, minus the one-click
button — never a degraded or partially-broken experience.

## 12. Rollout phases

- **P0** — `Table.location` migration; `S3BucketLogConfig` model + migration; `GetBucketLogging`
  discovery + manual-entry UI (§4.1, §4.2, §4.4); extend `fetch_s3_price_per_gb()`'s Price List
  API call to also cover request pricing (§6.4), with the pricing cache and the fallback-default
  UI label. This alone is a complete, usable feature for any org with the required (read-only)
  permission tier.
- **P1** — Trino external table registration + the partition-pruned, reconciling ingestion query;
  manual-trigger API endpoints; `TableCost` request-cost fields wired into the existing cost
  breakdown UI.
- **P2** — One-click auto-provision (§4.3) — the minimal-config convenience path for orgs that
  grant the optional write-permission tier; `DaemonRunner`-based scheduling once P1 has been
  validated against real buckets; Storage Lens as an alternate ingestion path for teams who'd
  rather not manage the Trino table.

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
