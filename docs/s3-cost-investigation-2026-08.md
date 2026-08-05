# S3 cost investigation — August 2026

**Goal:** reduce actual AWS S3 spend on the `pw-data-cdp-prod-*` buckets before end of August 2026.
**Status:** investigation in progress, no fixes shipped yet. This doc is the pickup point — read
this before re-deriving any of the below.

This is about **AWS's own S3 bill** (real dollars, pulled from the org's Cost & Usage Report),
not Cryo's own cost-modeling feature. If you're looking for how Cryo estimates *table-level*
storage/request cost internally, that's [`cost-savings.md`](cost-savings.md) and
[`s3-request-cost-tracking.md`](s3-request-cost-tracking.md) — unrelated to this doc, different
data source entirely.

## 0. Why this started

A daily cost-report webhook (Slack, `#cost-updates`-style) flagged
`pw-data-cdp-prod-gold-layer` for a WoW cost spike: **+27.3%** ($115.48/day → $146.95/day),
driven by `StandardStorage` (+41% WoW) and Tier-1 requests, i.e. PUT/LIST/COPY/POST (+32.3% WoW).
The bot's own root-cause line named two specific tables: `unified_bq.gold_dbt_pw_bq` (612k files,
661 snapshots) and `mview.dbt_gold_user_cohort_base_facts_free` (7.5k files, F-grade compaction).

Separately, `#cost-updates` noted bronze-layer already declined **-10% DoD / -20% WoW** purely
from S3 Intelligent-Tiering moving 30-day-cold data to Infrequent Access — an existing, proven
lever, just not applied everywhere.

## 1. How to pull this data yourself

### 1.1 There are two datasources both loosely called "cost-db" — only one works

Grafana (`https://platform-grafana.penpencil.co`) has two datasources that both surface under
searches for "cost":

| Name shown | UID | Type | Status |
|---|---|---|---|
| "Cost DB" (found via dashboard `AWS Cost`, `af75wzr9l6mf4d`, panel datasource) | `bdtp4f7ymy8zkd` | `mysql`, points at `10.29.98.180:3306` | **Broken / unreachable.** `/api/ds/query` against this UID hangs indefinitely (tested up to 100s, zero response) regardless of query — even `SELECT 1`. Don't waste time on it. |
| "cost-db" (as seen in **Explore**, `Home → Explore → cost-db`) | `ef75vmxq6mnswf` | `grafana-postgresql-datasource`, dataset `idp_prod` | **Works.** This is the real one. |

If you go hunting via `/api/search` or a dashboard's embedded panel JSON, you will find the dead
MySQL one first because it's what the older `AWS Cost` dashboard's panels reference. Use Explore
(or grab the UID below directly) instead of trusting a dashboard's panel datasource.

### 1.2 Querying it programmatically

Needs a Grafana service-account token (`Authorization: Bearer glsa_...`) with query permission —
ask whoever owns the Grafana org for one if you don't have it. Then:

```bash
TOKEN="glsa_..."
BASE="https://platform-grafana.penpencil.co"

curl -s --max-time 30 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/api/ds/query" -d '{
    "queries":[{
      "refId":"A",
      "datasource":{"type":"grafana-postgresql-datasource","uid":"ef75vmxq6mnswf"},
      "format":"table",
      "editorMode":"code",
      "dataset":"idp_prod",
      "rawQuery":true,
      "rawSql":"select line_item_usage_type, sum(line_item_unblended_cost) as cost from cloud_costs where line_item_usage_start_date >= now() - interval '"'"'30 days'"'"' and line_item_usage_account_id = '"'"'539436019547'"'"' and line_item_product_code = '"'"'AmazonS3'"'"' and line_item_resource_id = '"'"'pw-data-cdp-prod-gold-layer'"'"' group by line_item_usage_type order by cost desc"
    }],
    "range":{"from":"now-30d","to":"now"}
  }'
```

Response is Grafana's frame format: `results.A.frames[0].data.values` is a list of columns, each
a list of values (column-major, not row-major) — `values[0]` is every row's first selected field,
`values[1]` is every row's second, etc.

**Reliability caveat, seen directly this session:** the *exact same* query pattern that returned
in ~5s at one point returned zero response (hung past 20-40s) a few minutes later, for a
different bucket, then failed again on a health-check retry of the original working query. This
datasource is flaky under back-to-back calls — **don't fire several queries in rapid succession
and assume failures mean the SQL is wrong.** Space calls out, keep time windows narrow (7-30
days, not "all time"), and always filter `line_item_resource_id = '<exact-bucket>'` — a broad
`GROUP BY line_item_resource_id` with no bucket filter timed out every time it was tried (30s,
40s), even scoped to a single account and a 7-day window, while the same query with an exact
bucket match in the `WHERE` clause reliably returns in single-digit seconds. Likely no useful
index on `line_item_resource_id` for a full scan/group.

### 1.3 Schema (`cloud_costs` table, `idp_prod` dataset)

Standard AWS Cost & Usage Report (CUR) shape. Columns used so far:

- `line_item_usage_start_date` — timestamp, use for time filtering
- `line_item_usage_account_id` — AWS account ID (e.g. `539436019547`)
- `line_item_product_code` — service, e.g. `AmazonS3`, `AmazonEC2`, `AmazonCloudWatch`
- `line_item_resource_id` — for S3 line items, this is the **bucket name** (not an ARN); for
  EC2/EBS it's an instance/volume ID
- `line_item_usage_type` — the specific billed dimension, e.g. `APS3-TimedStorage-ByteHrs`,
  `APS3-Requests-Tier1`, `APS3-TimedStorage-INT-FA-ByteHrs` (see §1.4)
- `line_item_unblended_cost` — the dollar figure to sum

### 1.4 Reading `line_item_usage_type` for storage-class / tiering signal

This is the field that tells you whether a bucket has S3 Intelligent-Tiering active, without
needing AWS console/CLI access at all:

| Usage type substring | Meaning |
|---|---|
| `TimedStorage-ByteHrs` | Plain S3 Standard storage — **not** tiered |
| `TimedStorage-INT-FA-ByteHrs` | Intelligent-Tiering, Frequent Access tier — **same price as Standard**, just under the IT umbrella |
| `TimedStorage-INT-IA-ByteHrs` | Intelligent-Tiering, Infrequent Access — actually cheaper (~46% less than Standard) |
| `TimedStorage-INT-AA-ByteHrs` / `-AIA-` | Intelligent-Tiering Archive / Archive Instant Access tiers — cheaper still |
| `TimedStorage-GDA-ByteHrs` | Glacier Deep Archive |
| `TimedStorage-RRS-ByteHrs` | Reduced Redundancy (legacy) |
| `Requests-Tier1` | PUT / COPY / POST / LIST requests |
| `Requests-Tier2` | GET / SELECT and most other requests |
| `Requests-INT-Tier2` | GET/SELECT specifically against Intelligent-Tiering-class objects |
| `DataTransfer-Out-Bytes` | Egress |

**A bucket has Intelligent-Tiering enabled if and only if any `INT-` usage type shows up.** No
`INT-*` line items at all (only plain `TimedStorage-ByteHrs`) means it was never turned on for
that bucket.

## 2. Findings so far

### 2.1 Gold-layer has zero Intelligent-Tiering — confirmed via query, not assumed

60-day breakdown, `pw-data-cdp-prod-gold-layer`:

```
$3,323.85  APS3-TimedStorage-ByteHrs       ← 100% plain Standard, no INT-* at all
$2,881.36  APS3-Requests-Tier2
$  321.50  APS3-Requests-Tier1
$   27.38  APS3-TimedStorage-GDA-ByteHrs
$   17.00  APS3-Requests-GDA-Tier1
(+ small egress/misc lines, <$1 combined)
```

Compare `pw-data-cdp-prod-bronze-layer` (known control — already declining per the cost-bot):

```
$2,469.79  APS3-TimedStorage-INT-FA-ByteHrs  ← Intelligent-Tiering active
$2,094.83  APS3-TimedStorage-ByteHrs          ← untiered remainder
$  769.03  APS3-Requests-Tier2
$  693.32  APS3-Requests-Tier1
$  233.41  APS3-Requests-INT-Tier2
$   43.17  APS3-TimedStorage-INT-IA-ByteHrs   ← the actual cheaper tier
$   22.12  APS3-TimedStorage-GDA-ByteHrs
```

### 2.2 Important correction to an earlier assumption: Intelligent-Tiering's realized $ benefit is small, even where it's already on

Before pulling real numbers, the working assumption in this investigation was "enabling
Intelligent-Tiering on gold should behave like it did on bronze (-20% WoW), so flip it on."
**The data doesn't support that as a big August lever.** Look at bronze's own split above: even
with IT fully enabled, only **$43 of ~$4,608** in storage cost actually landed in the genuinely
cheaper Infrequent-Access tier. The "Frequent Access" tier — where $2,469.79 of bronze's cost
sits — is **priced identically to Standard**. IT only saves money once individual objects go 30+
days with zero access and roll into IA/Archive; for a bucket that's still mostly "frequent
access" priced, most of the theoretical saving hasn't materialized yet and may take months to
show up as more objects individually age past the threshold. (Per Shubham's note in
`#cost-updates`: bronze's *next* saving step is ~30 more days out, when IA-tier data starts
rolling into Archive Instant Access.)

**Conclusion:** enabling Intelligent-Tiering on gold is still correct to do (zero-effort,
zero-risk, purely additive), but don't count on it to move the August number by much. It's a
multi-month compounding lever, not an August lever.

### 2.3 Gold-layer storage cost is on a genuine upward trend, not a one-day blip

Daily `TimedStorage` cost, `pw-data-cdp-prod-gold-layer`, last 30 days (sparse sample):

| ~4 weeks ago | ~3 weeks ago | ~2 weeks ago | ~1 week ago | Most recent |
|---|---|---|---|---|
| $49.73/day | $52.30/day | $53.21/day | $67.95/day | $89.27/day peak, $75.04 latest |

Roughly a 50-80% climb over the past month. There's a ~7-day sawtooth (cost drops every ~7 days,
presumably some periodic cleanup already happening), but both the troughs and the peaks trend
upward cycle over cycle — whatever cleanup is already running isn't keeping pace.

### 2.4 The likely mechanism: `gold_dbt_pw_bq`'s snapshot/file bloat

`unified_bq.gold_dbt_pw_bq` — the table named in the original incident — is the same 24TB table
Cryo already ran a deep-mode maintenance pilot against earlier this cycle (see PDAE-8269;
verified via direct Spark driver-log inspection that `optimize`/binpack alone drove 100% of that
run's ~257.6GB net size reduction). **It's now back up to 612k files and 661 retained snapshots**,
and driving 378k-476k S3 PUT operations *per day* on its own — the single largest line item in
the whole bucket's Tier-1/Tier-2 request cost.

This is the actual takeaway: **a one-off maintenance pass does not hold against a table writing
this fast.** Old snapshots keep their manifests and data files alive in Standard storage
(unpriced-differently, full Standard rate) until `expire_snapshots` prunes them — 661 retained
snapshots strongly suggests retention hasn't been enforced on a working cadence. Unlike
Intelligent-Tiering, `expire_snapshots` + `remove_orphan_files` **deletes** genuinely dead bytes
outright rather than re-pricing them into a cheaper tier — faster, more certain savings, and it's
squarely something Cryo already knows how to do.

`mview.dbt_gold_user_cohort_base_facts_free` (7.5k files, F-grade, brand-new PUT spike this week,
0→144,566) is a second, smaller instance of the same disease — never touched by Cryo maintenance
yet.

**Neither of these tables is in Cryo's current cold-table cleanup cohort** — that campaign
explicitly targets zero-query tables (`query_count_30d <= 0`), and both of these are actively
written and actively queried. This is Wave 2 territory (PDAE-8276), not the current campaign.

### 2.5 Rough projection

If gold's storage-cost growth continues unchecked at the observed rate vs. if it gets pinned back
near its ~3-4-week-ago baseline (via a real retention cadence on `gold_dbt_pw_bq`), the spread
over the remaining days of August is roughly **$800-1,400 for this one bucket** — bigger and
better-evidenced than an earlier off-the-cuff "$400-750/month" guess, because it's derived from
the actual 30-day trend rather than an analogy to bronze.

This is **not verified against a live AWS S3 console check** (Intelligent-Tiering config,
lifecycle rules) — attempts to reach `aws s3api` from the working session hit an invalid/expired
token; someone with working AWS creds should confirm directly. It's also not yet extended to
`pw-data-cdp-prod-silver-layer` (flagged +12.2% WoW in the same report, never drilled into),
`pw-data-cdp-prod-temp`, or the `nebula-jar`/`logs` buckets — attempts to pull those hit the
datasource-flakiness issue in §1.2 and weren't completed.

## 3. Levers, ranked by confidence/impact for August specifically

1. **Scheduled/continuous retention on `gold_dbt_pw_bq`** (highest confidence, highest impact).
   Not another one-off run — this table's write rate means a single pass doesn't hold. Needs a
   recurring `expire_snapshots` + `remove_orphan_files` cadence (hourly or a few times a day, not
   daily/weekly) sized to its actual write frequency. Real ticket, not yet created — see §4.
2. **Same treatment for `dbt_gold_user_cohort_base_facts_free`** — smaller table, same disease,
   cheap to bundle into the same effort.
3. **Enable Intelligent-Tiering on `pw-data-cdp-prod-gold-layer`** (and check
   `silver`/`temp` too) — zero-effort, zero-risk, correct to do, but per §2.2, don't expect it to
   move August's number much on its own. Do it anyway, just don't lead with it.
4. **Ongoing cold-table cleanup (11K zero-query tables)** — real and already running, but
   diffuse and small in dollar terms (~$25-50/month estimated from the verified -11%
   storage-size sample). Reposition internally as hygiene/reliability, not the August cost story
   — the report itself shows the real driver is a small number of actively-written hot tables,
   not the cold cohort.
5. **Trino cost spike (+8.5% WoW)** — the cost-bot's own root-cause attribution points at new
   query patterns (OpenMetadata profiling, DBeaver ETL), not file layout. Not a confident Cryo
   lever for August; don't count on it, though better-compacted tables may give a secondary,
   unquantified assist on Trino scan cost as a side effect of #1/#2.

## 4. Next steps (not yet done)

- [ ] Create a Jira ticket for scheduled/recurring retention on `gold_dbt_pw_bq` specifically
      (distinct from PDAE-8276's broader Wave 2 scope) — this is the top-priority item above.
- [ ] Get someone with working AWS console/CLI access to confirm Intelligent-Tiering status on
      `pw-data-cdp-prod-gold-layer` directly (cross-check against §2.1's inferred-from-billing-data
      conclusion) and flip it on if confirmed off.
- [ ] Extend the §2 breakdown to `silver-layer`, `temp`, `logs`, `nebula-jar` — silver was flagged
      +12.2% WoW in the same report and was never drilled into.
- [ ] Re-run the `line_item_usage_type` breakdown for gold in a few weeks once the retention fix
      is live, to confirm the growth trend actually flattens (this doc's §2.3 table is the
      before-state to compare against).
