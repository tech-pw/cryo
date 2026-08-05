# Cryo service-level objectives

These objectives define the repository-owned signals and production evidence required before
durable maintenance intents can be enabled by default. Local tests prove the contracts, not the
production SLOs. Production evidence must come from Prometheus over the stated windows.

## Control-plane API

| Signal | Objective | Prometheus evidence |
|---|---:|---|
| Availability | >= 99.9% successful responses over 30 days | `1 - sum(rate(cryo_api_requests_total{status_class="5xx"}[30d])) / sum(rate(cryo_api_requests_total[30d]))` |
| Latency | p95 < 500 ms over 30 days, excluding streaming endpoints | `histogram_quantile(0.95, sum by (le) (rate(cryo_api_request_duration_seconds_bucket[30d])))` |

API labels use FastAPI route templates rather than concrete paths, so table, run, and job IDs do
not create unbounded Prometheus cardinality.

## Safety kernel

| Signal | Objective | Source |
|---|---:|---|
| Duplicate destructive effects | 0 confirmed duplicates | Effect receipts, engine references, and audit review |
| Attempt terminal accounting | 100% of started attempts reach an explicit outcome | `cryo_maintenance_attempts_started_total` and `cryo_maintenance_attempt_results_total` |
| Unknown effect visibility | 100% represented in the reconciliation backlog | `cryo_maintenance_reconciliation_backlog` |
| Fenced execution | 0 accepted stale-fence effects | durable intent/fence tests plus production audit export |

Prometheus counters are operational signals, while Postgres intent, attempt, receipt, and audit rows
remain the durable evidence of record.

## Pause propagation

**Objective:** a pause is observed by the final pre-effect guard in under 5 seconds at p99.

```promql
histogram_quantile(
  0.99,
  sum by (le) (rate(cryo_maintenance_pause_propagation_seconds_bucket[30d]))
)
```

`cryo_maintenance_pause_blocks_total` counts effects stopped by global, tenant, or destination
scope. The timer starts at the persisted control row's update time and ends when the effect guard
observes it. API acknowledgement alone is not pause-propagation evidence.

## Uncertain-effect recovery

**Objective:** no submitted/unknown effect eligible for reconciliation remains unresolved for more
than 15 minutes (the durable-effect RPO).

- `cryo_maintenance_reconciliation_backlog` is the exact due backlog at the start of each pass.
- `cryo_maintenance_reconciliation_oldest_age_seconds` is the oldest due receipt age.
- `cryo_maintenance_reconciliation_outcomes_total` separates resumed, waiting, manual-review, and
  superseded outcomes.
- The reconciler never replays an unproven effect. Manual strategies remain held for an operator.

## Correlation contract

Every API request receives bounded `X-Request-ID` and `X-Correlation-ID` values. A maintenance
run persists the root correlation ID in `runs.summary.correlation_id`; Governor, Worker, effect
execution, and reconciliation restore it alongside run, job, intent, attempt, effect, table,
destination, engine-reference, and snapshot identifiers. External IDs are limited to 128 safe
characters to prevent log injection and oversized labels.

Correlation IDs belong in logs and traces, never Prometheus labels.

## Operations

- Dashboard: **Cryo Safety & SLOs** (`deploy/grafana/dashboards/cryo-safety-slos.json`).
- Alert rules: `deploy/prometheus/rules/cryo-safety.yml`.
- API serves `/metrics`; worker, Governor, scheduler, and scanner health servers now serve the same
  endpoint.
- Helm pods are annotated for Prometheus pod discovery. The local observability compose stack
  statically scrapes all background services.

Before enabling durable intents by default, attach a 30-day SLO report and the production-shaped
10,000-attempt crash campaign to the Wave 1 graduation evidence. Do not substitute a local run.
