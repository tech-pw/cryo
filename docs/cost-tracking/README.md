# Cost tracking — daily log + month-over-month comparison

**Goal**: reduce this month's infra cost vs last month's, with statistical significance —
not just "the number went down," but confidence the change is real and not day-to-day noise.
Standing directive: see `agents.md` § Learned User Preferences ("check cost every day").

## What's tracked

Two namespaces on the `data-platform-eks` cluster, via AWS's EKS Split Cost Allocation
Data (the same mechanism documented in `agents.md`'s "Cost self-check" entry):

- **`trino-db`** — Trino's own compute cost. This is the metric that answers "is the
  fleet's own query/compute efficiency improving" (fewer small files after compaction →
  cheaper scans).
- **`cryo`** — Cryo's own infra footprint. Answers a different question: "is Cryo itself
  being wasteful," independent of how well it's optimizing the fleet.

Also tracked less frequently (monthly rollups, not daily — this data doesn't change fast
enough to need daily granularity): S3 storage + request cost for Cryo-managed buckets,
via the `cloud_costs` table in the Postgres cost datasource (`ef75vmxq6mnswf`).
**Must filter `line_item_resource_id` to the actual warehouse bucket** (e.g.
`pw-data-cdp-prod-gold-layer`, from the source's `config.warehouse` in Cryo's own
`/api/v1/sources`) — confirmed live 2026-08-10: an unfiltered `AmazonS3` query over the
whole `cloud_costs` table (company-wide CUR export, not scoped to any one namespace)
returned ~10x the bucket-scoped number ($82K storage / $30K requests over 30d vs $2.9K /
$1.5K once filtered to the real bucket). The 2026-08-06 baseline row's exact filter isn't
recorded, so don't treat a comparison against it as apples-to-apples — the 2026-08-10 row
onward uses this bucket-scoped query and is the reliable baseline going forward.

## How to check (the query)

Against the `grafana-athena-datasource` (uid `fe0bimj2kv9xca`), `/api/ds/query`:

```sql
select date_format(line_item_usage_start_date, '%Y-%m-%d') as day,
       sum(split_line_item_split_cost) + sum(split_line_item_unused_cost) as total_cost
from cur
WHERE date_format(line_item_usage_start_date, '%Y-%m-%d') >= date_format(date_add('day', -45, now()), '%Y-%m-%d')
  and resource_tags_aws_eks_cluster_name = 'data-platform-eks'
  and resource_tags_aws_eks_namespace = '<trino-db|cryo>'
GROUP BY date_format(line_item_usage_start_date, '%Y-%m-%d')
ORDER BY day
```

Pull the last ~10 days each time (enough to catch anything missed since the last check;
the log is append-and-dedupe, not append-only-blind).

## How to log it

Append rows to `daily-cost-log.csv` (schema: `date,namespace,daily_cost_usd,source,checked_at`).
**Whenever a cost check happens — whether the user asks or it's a self-initiated daily
check per the standing directive — write the numbers here before reporting them.** This
is what makes "someone remembering to ask each day" actually work over time: the answer
doesn't depend on what's still in a given conversation's context.

- `source` should say what query/table backed the number (e.g. `cur:split+unused` for the
  Athena EKS query above, `cur_postgres:cloud_costs_30d_total` for the S3 rollup) —
  methodology changes over time, and this makes old rows self-documenting instead of
  silently drifting in meaning.
- If a `(date, namespace)` pair already has a row, overwrite it rather than duplicating —
  same day, re-checked, use the latest pull (CUR data itself can lag/backfill).

## How to compare (the significance test)

Run `compare_mom.py <namespace>` (no dependencies beyond the standard library). It:

1. Compares the current month's day-to-date average against **the same day-range** in the
   previous month — not full-month totals. Comparing August's first 5 days against July's
   full 31-day average is not a fair comparison (confirmed live, 2026-08-06: doing it that
   loose way said Trino was down 21%; the same-day-range comparison said +5.8%, not
   significant — the loose version was simply wrong, not just less precise).
2. Runs a Welch's t-test (unequal variance, small samples) — no scipy dependency, plain
   `statistics`/`math`.
3. **Refuses to call anything "significant" with fewer than 7 days of current-month data**,
   regardless of what the t-statistic says — the test is mathematically valid but
   underpowered that early, and an early lucky/unlucky run of days will produce a
   misleadingly large t-stat on tiny n.

## What "success" looks like

- Trino (`trino-db`): month-over-month average daily cost trending down, confirmed by
  `compare_mom.py` reaching >=7 days of current-month data with a significant result.
- Cryo (`cryo`): NOT necessarily trending down in absolute terms right now — real
  fleet-wide maintenance work (deep-mode runs, orphan cleanup) legitimately costs more to
  run than doing nothing. What matters here is the *unused_cost* ratio (see `agents.md`'s
  cost self-check) trending toward 0, not the raw total. A rising raw Cryo cost with a
  falling unused-cost ratio is Cryo doing more real work efficiently, not regressing.
