# Cryo Operator Runbook

Operational guide for on-call engineers running Cryo in production.

## Quick health checks

```bash
# API
curl -sf http://<api-host>/healthz

# Prometheus metrics
curl -sf http://<api-host>/metrics | grep -E '^cryo_(governor|worker|scanner|queue)'

# Grafana dashboard: Cryo Overview (uid: cryo-overview)
# Panels: admission decisions, queue depth, worker duration, scanner throughput, cost estimates
```

Log exploration (Loki via Grafana Explore):

```logql
{service=~"cryo-.*"} |= "ERROR"
```

---

## Governor stuck / not admitting jobs

### Symptoms

- Maintenance jobs remain `queued` for extended periods
- `cryo_governor_queue_depth` rising in Grafana
- `cryo_governor_admission_decisions_total{outcome="deferred"}` increasing
- No new `cryo_worker_job_executions_total`

### Diagnosis

1. **Check Governor logs**
   ```logql
   {service="cryo-governor"} |= "wave"
   ```
2. **Engine load** — Governor defers when Trino/Spark `system.runtime.queries` exceeds caps.
   - Query Trino: `SELECT state, count(*) FROM system.runtime.queries GROUP BY 1`
3. **Redis leader lock** — multiple Governor pods; only the leader admits.
   ```bash
   redis-cli GET cryo:leader:governor
   ```
4. **Off-peak window** — admissions may defer outside configured IST off-peak hours.
5. **Per-replica caps** — `default_per_replica_optimize_cap` may be saturated.

### Remediation

| Cause | Action |
|-------|--------|
| Engine overloaded | Wait for off-peak or reduce external query load; temporarily lower worker replicas |
| Stale leader lock | Restart Governor pod; verify Redis connectivity |
| Misconfigured destination | Check destination `enabled=true` and probe credentials |
| Orphaned in-flight jobs | Governor reconciles on wave; inspect `audit_logs` for stuck `running` jobs |
| Emergency bypass | Use ad-hoc maintenance with `force=true` (audited) for a single table only |

### Escalation data to collect

- Governor wave log excerpt (last 3 waves)
- `cryo_governor_admission_decisions_total` by outcome (24h)
- Destination engine query count snapshot
- Redis queue depth: `cryo_queue_pending_jobs`

---

## Scanner failures

### Symptoms

- Tables show stale `scan_at` timestamps
- `cryo_scanner_errors_total` increasing
- `cryo_scanner_tables_scanned_total` flatlined
- UI scanner status shows failed runs

### Diagnosis

1. **Scanner run logs**
   ```logql
   {service="cryo-scanner"} |= "ScanSummary" or |= "ERROR"
   ```
2. **Catalog connectivity** — PyIceberg REST / Glue / Hive reachability from scanner pod
3. **Source credentials** — `secret_ref` resolution failures appear as auth errors
4. **Chunk parallelism** — OOM on large catalogs; check `scan_parallel_tables` / memory limits
5. **Manual trigger**
   ```bash
   curl -X POST http://<api>/api/v1/scans \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"source_id":"<uuid>","full_scan":true}'
   ```

### Remediation

| Cause | Action |
|-------|--------|
| Catalog timeout | Retry scan; increase scanner memory; reduce `num_chunks` temporarily |
| Auth failure | Rotate credentials in secret store; verify IRSA/SA role |
| Single table error | Scanner isolates per-table failures; inspect `audit_logs` / table detail |
| Scheduler disabled | PATCH `/api/v1/scanner/config` → `enabled: true` |
| Full cost scan overload | Disable `cost_full_scan` until off-peak |

---

## Approval workflows

Cryo can require human approval before destructive or high-impact maintenance.

### Symptoms

- Jobs stuck in `pending_approval`
- `cryo_scheduler_approval_requests_total{status="pending"}` rising
- Slack/email approval links not actioned

### Workflow

1. List pending approvals: `GET /api/v1/advanced/approvals?status=pending`
2. Review `impact_preview` payload (estimated bytes, operations, tables affected)
3. Approve or reject:
   - `POST /api/v1/advanced/approvals/{id}/approve`
   - `POST /api/v1/advanced/approvals/{id}/reject`
4. Approved jobs enqueue automatically; rejected jobs audit with reason

### SLA guidance

- **Standard optimize** — approve within 4 business hours
- **expire_snapshots / remove_orphan_files** — require explicit operator sign-off; verify retention policy
- **Expired approvals** — re-trigger maintenance; expired requests increment `status="expired"`

---

## Metrics reference

| Metric | Type | Meaning |
|--------|------|---------|
| `cryo_governor_admission_decisions_total` | Counter | admitted / deferred / blocked / orphaned |
| `cryo_governor_queue_depth` | Gauge | Jobs waiting for admission |
| `cryo_queue_pending_jobs` | Gauge | Redis queue depth by destination |
| `cryo_worker_job_duration_seconds` | Histogram | End-to-end job execution time |
| `cryo_worker_job_executions_total` | Counter | Jobs by status (ok/error/timeout) |
| `cryo_scanner_tables_scanned_total` | Counter | Tables scanned per source |
| `cryo_scanner_errors_total` | Counter | Scanner failures by error type |
| `cryo_cost_monthly_estimate_usd` | Gauge | Estimated monthly cost by source/type |
| `cryo_cost_savings_projected_usd` | Gauge | Projected savings from maintenance |
| `cryo_anomaly_alerts_total` | Counter | Dispatched anomaly alerts |

### Useful PromQL

```promql
# Admission defer rate
sum(rate(cryo_governor_admission_decisions_total{outcome="deferred"}[15m]))

# Worker p95 latency
histogram_quantile(0.95, sum by (le) (rate(cryo_worker_job_duration_seconds_bucket[15m])))

# Scanner error rate
sum(rate(cryo_scanner_errors_total[1h]))
```

---

## Anomaly alerts

When `detect_all_anomalies(..., dispatch_alerts=True)` runs (scheduled job or API with dispatch enabled):

- **Slack** — all severities ≥ configured minimum via `CRYO_SLACK_WEBHOOK_URL`
- **PagerDuty** — high-severity only via `CRYO_PAGERDUTY_ROUTING_KEY`

Verify dispatch:

```bash
curl "http://<api>/api/v1/advanced/anomalies?lookback_days=30&threshold_sigma=3.0"
```

Check `cryo_anomaly_alerts_total` after dispatch.

---

## Rate limiting (API)

Expensive endpoints are token-bucket limited per client IP:

- `POST /api/v1/maintenance/preview`
- `POST /api/v1/scans`
- `GET /api/v1/advanced/anomalies`
- `POST /api/v1/routing/preview`
- `POST /api/v1/tables/{id}/clustering-plan`

HTTP 429 responses include `Retry-After: 2`. Tune via `CRYO_API_RATE_LIMIT_RPS` and `CRYO_API_RATE_LIMIT_BURST`.

---

## Multi-tenant isolation

After migration `20260705_0004_multi_tenant`:

1. Create tenant rows in `cryo.tenants`
2. Backfill `tenant_id` on `sources`, `destinations`, `tables`, `presets`
3. Ensure JWT carries `tenant_id`; API sessions use `create_tenant_aware_session`
4. Cross-tenant access returns 404 via `verify_tenant_access`

---

## Related docs

- [`docs/DEPLOYMENT_GUIDE.md`](./DEPLOYMENT_GUIDE.md)
- [`docs/operator-runbook.md`](./operator-runbook.md) — local dev procedures
- [`lld.md`](../lld.md) — API and schema contracts
