---
title: Configuration
description: Configuration reference for Cryo. All CRYO_* environment variables from the Settings class and the code that reads os.environ, and the top-level Helm chart values.
canonical: https://tech-pw.github.io/cryo/configuration/
updated: 2026-10-26
---

# Configuration

Every CRYO_* environment variable the code reads, with its default, and every top-level Helm value. Both tables are generated from the code, so they match the release you build from.

Cryo reads its settings from environment variables with the prefix `CRYO_`. A field `secret_key` is the variable `CRYO_SECRET_KEY`. Lists and maps are JSON, for example `CRYO_CORS_ALLOW_ORIGINS='["https://cryo.example.com"]'`. Locally, `.env` in the working directory is read too.

On Kubernetes, put plain values in `global.env` and secrets in the Secret that `global.envFromSecret` names. Every Cryo process, and the migration Job, gets both. See [Install on Kubernetes](https://tech-pw.github.io/cryo/install/).

Settings that matter for security

`CRYO_ENVIRONMENT`, `CRYO_SECRET_KEY`, `CRYO_JWT_SECRET`, `CRYO_ALLOW_DEV_TOKEN_IN_PROD` and the `CRYO_OIDC_*` settings decide who can sign in and what an attacker can forge. They're marked below. [Security](https://tech-pw.github.io/cryo/security/) explains each one.

## Environment variables

Defaults are the code defaults. The chart sets a few of its own in `global.env`, such as `CRYO_SCAN_ENGINE=go` and `CRYO_SCAN_TABLE_TIMEOUT_SECONDS=600`.

232 settings, grouped by the first word of their name.

### CRYO_AI_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_AI_FEATURE_ANOMALY_BRIEF_ENABLED` | `true` | Enable anomaly root-cause brief surfaces. |

| `CRYO_AI_FEATURE_DIAGNOSIS_ENABLED` | `true` | Enable run-failure diagnosis API/UI surfaces. |

| `CRYO_AI_FEATURE_FILTER_TRANSLATION_ENABLED` | `true` | Enable natural-language table-filter translation. |

| `CRYO_AI_FEATURE_PLAN_NARRATION_ENABLED` | `true` | Enable optional plan narration for SQL intent in PlanPreview. |

### CRYO_ANOMALY_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_ANOMALY_DETECTION_BATCH_SIZE` | `100` | Tables processed per batch in fleet-wide anomaly detection. |

| `CRYO_ANOMALY_HISTORY_ROWS_PER_TABLE` | `60` | Max history rows loaded per table during anomaly detection. |

| `CRYO_ANOMALY_MAX_RESULTS` | `500` | Maximum anomaly alerts retained from a fleet-wide scan. |

### CRYO_ASSISTANT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_ASSISTANT_ENABLED` | `false` | Master switch for Ask Cryo. Stays off until an operator enables it. |

| `CRYO_ASSISTANT_MAX_ACTIVE_TURNS_PER_USER` | `2` | *Not described yet.* |

| `CRYO_ASSISTANT_MAX_PARALLEL_READS` | `4` | *Not described yet.* |

| `CRYO_ASSISTANT_MAX_TOOL_CALLS` | `8` | *Not described yet.* |

| `CRYO_ASSISTANT_MCP_ENABLED` | `false` | Expose the streamable HTTP MCP adapter. Off until BOT-09 is qualified. |

| `CRYO_ASSISTANT_PROPOSAL_TTL_S` | `600` | *Not described yet.* |

| `CRYO_ASSISTANT_RETENTION_DAYS` | `90` | *Not described yet.* |

| `CRYO_ASSISTANT_TURN_TIMEOUT_S` | `45` | *Not described yet.* |

| `CRYO_ASSISTANT_WRITES_ENABLED` | `false` | Assistant-only kill switch, separate from maintenance_writes_enabled. |

### CRYO_AUDIT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_AUDIT_LOG_PRUNE_BATCH_SIZE` | `10000` | Maximum audit rows deleted in one transaction. |

| `CRYO_AUDIT_LOG_PRUNE_MAX_ROWS_PER_TICK` | `100000` | Maximum audit rows deleted by one daily leader-scheduler retention tick. |

| `CRYO_AUDIT_LOG_RETENTION_DAYS` | `365` | Days to retain immutable maintenance audit entries in the operational database. The leader scheduler prunes older rows in bounded batches. Set a longer value when regulatory or incident-response policy requires it; 365 days is the safe default. |

### CRYO_COST_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_COST_CEILING_CHECK_INTERVAL_MINUTES` | `30` | How often the scheduler re-checks real spend. CUR data lags 24-48h, so checking much more often than this doesn&#x27;t reveal new information -- this mainly controls how quickly a pause gets lifted once yesterday&#x27;s lagged data catches up to show spend back under the ceiling. |

| `CRYO_COST_CEILING_CLUSTER_NAME` | `""` | EKS cluster name as tagged in AWS CUR (resource_tags_aws_eks_cluster_name). Required when cost_ceiling_enabled is true. |

| `CRYO_COST_CEILING_DAILY_USD` | `250.0` | Daily used+unused USD ceiling for cost_ceiling_enabled&#x27;s circuit breaker. |

| `CRYO_COST_CEILING_ENABLED` | `false` | Enables cryo.reporting.cost_ceiling&#x27;s real-dollar circuit breaker -- checks the cryo namespace&#x27;s actual daily spend (used+unused, via Grafana&#x27;s Athena CUR datasource) and pauses fleet-wide admission (the same MaintenanceControlState global pause an operator&#x27;s manual pause button writes to) if it exceeds… |

| `CRYO_COST_CEILING_NAMESPACE` | `"cryo"` | Kubernetes namespace to check against cost_ceiling_daily_usd. |

| `CRYO_COST_CEILING_TOLERANCE_PCT` | `0.15` | Fraction above cost_ceiling_daily_usd that&#x27;s treated as normal variance, not a breach worth halting the fleet for -- e.g. 0.15 means spend up to 15% over the ceiling only logs a warning; only spend beyond that pauses admission. |

| `CRYO_COST_FULL_SCAN_EVERY_HOURS` | `168` | *Not described yet.* |

| `CRYO_COST_METADATA_MAX_FILES_ESTIMATE` | `2000000` | Cheap pre-filter before even attempting Tier-1 metadata cost accounting: skip it when the current-snapshot file count meets or exceeds this threshold. |

| `CRYO_COST_METADATA_MAX_MANIFESTS_CURRENT` | `500` | Skip Tier-1 metadata cost when current-snapshot manifest count meets or exceeds this threshold. |

| `CRYO_COST_MODEL_REFRESH_INTERVAL_HOURS` | `6` | Hours between cost-model retrains (cryo.ml.cost_integration.refresh_cost_model_cache). |

| `CRYO_COST_ORPHAN_MIN_AGE_HOURS` | `72` | *Not described yet.* |

| `CRYO_COST_PRICE_PER_GB_MONTH` | `0.023` | *Not described yet.* |

| `CRYO_COST_SCAN_MAX_LIST_KEYS` | `1000000` | *Not described yet.* |

| `CRYO_COST_SCAN_MAX_MANIFESTS` | `5000` | *Not described yet.* |

| `CRYO_COST_SCAN_MAX_SNAPSHOTS` | `200` | *Not described yet.* |

| `CRYO_COST_SCAN_TIER` | `"metadata"` | *Not described yet.* |

| `CRYO_COST_USE_GIB` | `true` | *Not described yet.* |

### CRYO_DEFAULT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_DEFAULT_EMR_SLOT_CAP` | `10` | *Not described yet.* |

| `CRYO_DEFAULT_NATIVE_SLOT_CAP` | `5` | *Not described yet.* |

| `CRYO_DEFAULT_PER_REPLICA_OPTIMIZE_CAP` | `5` | *Not described yet.* |

| `CRYO_DEFAULT_RETAIN_LAST` | `1` | *Not described yet.* |

| `CRYO_DEFAULT_RETENTION_DAYS` | `7` | *Not described yet.* |

### CRYO_GITOPS_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_GITOPS_POLICY_DIR` | `"policies"` | Relative directory within gitops_repo_path containing YAML policies. |

| `CRYO_GITOPS_REPO_PATH` | `unset` | Local path to GitOps policy repository for webhook/poll sync. |

| `CRYO_GITOPS_WEBHOOK_SECRET` | `unset` | HMAC secret for GitOps webhook signature verification. |

### CRYO_JOB_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_JOB_PRUNE_BATCH_SIZE` | `10000` | Maximum job rows deleted in one transaction. |

| `CRYO_JOB_PRUNE_MAX_ROWS_PER_TICK` | `100000` | Maximum job rows deleted by one daily retention tick. |

| `CRYO_JOB_RETENTION_DAYS` | `180` | Days to retain finished MaintenanceJob rows (status terminal, finished_at set). Had no retention at all until 2026-10-05. Only ever deletes rows with a non-null finished_at, so queued/admitted/running jobs are never eligible. |

### CRYO_LAYOUT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_LAYOUT_AUTOPILOT_ENABLED` | `false` | Off by default -- opt-in, and a second gate on top of the per-preset opt-in below. |

| `CRYO_LAYOUT_AUTOPILOT_MAX_TABLES_PER_TICK` | `20` | Cap on how many tables get their pending layout recommendation auto-executed per telemetry tick when layout_autopilot_enabled is true. Kept far lower than property_autopilot_max_tables_per_tick since each one is a real Spark rewrite job, not a metadata-only property flip. |

| `CRYO_LAYOUT_EFFECTIVENESS_MAX_TABLES_PER_TICK` | `20` | Cap on how many applied-but-unmeasured LayoutRecommendation rows get a fresh observed_prune_improvement reading per telemetry tick. |

| `CRYO_LAYOUT_RECOMMENDATION_MAX_TABLES_PER_TICK` | `20` | Cap on how many tables get a layout-recommendation (re)generated per telemetry tick. generate_layout_recommendation() skips tables refreshed within the last 24h on its own, so this only bounds the cost of the *first* pass over a large backlog -- keeps a single tick from doing thousands of analyses (each with its own… |

### CRYO_LLM_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_LLM_ALLOWED_ORIGINS` | `""` | Comma-separated exact origins that a saved OpenAI-compatible base URL may select, such as http://localhost:11434. The request URL is rebuilt from the matching origin. api.openai.com and *.openai.azure.com stay allowed without an entry here. Set CRYO_LLM_ALLOWED_ORIGINS. **Security:** Each listed origin can receive AI provider requests. Keep it short. |

| `CRYO_LLM_DAILY_COST_CAP_USD_DEFAULT` | `15.0` | Default per-provider daily spend cap in USD (best-effort; derived from provider config pricing). |

| `CRYO_LLM_DAILY_TOKEN_CAP_DEFAULT` | `300000` | Default per-provider daily token cap when provider budget omits it. |

| `CRYO_LLM_DEFAULT_MODEL_ANTHROPIC` | `"claude-sonnet-4-20250514"` | Default Anthropic model when provider row omits model. |

| `CRYO_LLM_DEFAULT_MODEL_AZURE_OPENAI` | `"gpt4o"` | Default Azure OpenAI deployment when provider row omits model. |

| `CRYO_LLM_DEFAULT_MODEL_OPENAI` | `"gpt-4o"` | Default OpenAI model when provider row omits model. |

| `CRYO_LLM_OPENAI_BASE_URL` | `unset` | Optional OpenAI-compatible API base override (vLLM/Ollama/local gateways). When set, OpenAI provider calls are routed through this endpoint. A non-OpenAI/Azure origin is used only if it is listed in CRYO_LLM_ALLOWED_ORIGINS. |

| `CRYO_LLM_OVER_BUDGET_MODE` | `"abstain"` | Behavior when an LLM provider exceeds budget: hard reject or abstain-with-fallback. |

| `CRYO_LLM_PER_CALL_MAX_TOKENS_DEFAULT` | `1200` | Default max completion tokens allowed per LLM call when provider budget omits it. |

### CRYO_MAINTENANCE_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_MAINTENANCE_AUTOCOMP_BUDGET_GBHR` | `unset` | Compute budget per scheduled run. Unset: max_lanes x lane memory x the hours lanes may start tables. |

| `CRYO_MAINTENANCE_AUTOCOMP_HEAT_WEIGHT` | `0.3` | Cryo extension: share of the score from Trino query heat; 0 = paper-only. |

| `CRYO_MAINTENANCE_AUTOCOMP_REWRITE_GB_PER_HOUR` | `25.0` | RewriteBytesPerHour until a run has measured it; afterwards the median of recent runs&#x27; observed rewrite throughput is used. |

| `CRYO_MAINTENANCE_AUTOCOMP_W_FILE_REDUCTION` | `0.7` | Weight of file-count reduction in the AutoComp table ranking. The compute-cost weight is one minus this value. |

| `CRYO_MAINTENANCE_COOLDOWN_HOURS` | `20` | *Not described yet.* |

| `CRYO_MAINTENANCE_DESTINATION_ID` | `unset` | Spark destination to run on; defaults to the only enabled Spark one. |

| `CRYO_MAINTENANCE_DURATION_MODEL` | `"rate"` | How AutoComp estimates a table&#x27;s rewrite hours. &#x27;rate&#x27;: GiB / RewriteBytesPerHour. &#x27;ernest&#x27;: the Ernest model fitted on completed rewrite statements (cryo/maintenance/ernest.py), used only while it beats &#x27;rate&#x27; on held-out runs; the comparison is recorded in every run&#x27;s autocomp trace either way. |

| `CRYO_MAINTENANCE_LANE_CAPACITY_TYPES` | `"spot,on-demand"` | Karpenter capacity types lane pods may run on, most preferred first. |

| `CRYO_MAINTENANCE_LANE_MAX_CPU_MILLI` | `4000` | Upper bound on one batch-maintenance lane pod&#x27;s CPU, in millicores. |

| `CRYO_MAINTENANCE_LANE_MAX_MEM_MIB` | `16384` | Upper bound on one batch-maintenance lane pod&#x27;s memory, in MiB. |

| `CRYO_MAINTENANCE_LANE_MAX_NODE_CPU` | `0` | Optional inclusive Karpenter node vCPU ceiling for maintenance lanes. Zero disables it; enabling requires karpenter.k8s.aws/instance-cpu node labels. Prevents small protected lanes from pinning very large shared nodes. |

| `CRYO_MAINTENANCE_LANE_TARGET_GB` | `200.0` | Rewrite GB one lane should carry before another pod is worth starting. |

| `CRYO_MAINTENANCE_MAX_LANES` | `4` | Max Spark pods. |

| `CRYO_MAINTENANCE_MAX_LANE_RESPAWNS` | `8` | Replacement pods one run may start for lanes that died mid-run (OOM, node loss). The table a lane died on is recorded as failed; the replacement takes the rest. |

| `CRYO_MAINTENANCE_MAX_RUNTIME_MINUTES` | `360` | Hard stop for the whole run; lanes stop starting tables 20 min earlier. |

| `CRYO_MAINTENANCE_MAX_STATS_AGE_DAYS` | `3` | Skip tables whose last successful scan is older than this. |

| `CRYO_MAINTENANCE_MEASURE_ORPHAN_BYTES` | `false` | Before each remove_orphan_files, run the same call as a dry run and sum the sizes of the files it lists, so orphan removal&#x27;s audit rows carry actual_bytes_freed. Off by default: the dry run doubles the orphan LIST, which has OOM&#x27;d Spark before. |

| `CRYO_MAINTENANCE_POLL_SECONDS` | `30` | *Not described yet.* |

| `CRYO_MAINTENANCE_RESCAN_ENABLED` | `true` | *Not described yet.* |

| `CRYO_MAINTENANCE_SHARED_SNAPSHOT_MAX_AGE_HOURS` | `72` | If the shared-S3-path snapshot cannot be refreshed and the last one is older than this, the run skips remove_orphan_files on every table. |

| `CRYO_MAINTENANCE_SLACK_ENABLED` | `true` | *Not described yet.* |

| `CRYO_MAINTENANCE_TOP_N` | `500` | Tables per batch run. |

| `CRYO_MAINTENANCE_WRITES_ENABLED` | `true` | When false, maintenance cannot enqueue or execute Iceberg writes. |

### CRYO_OIDC_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_OIDC_AUDIENCE` | `unset` | Expected access-token audience; defaults to oidc_client_id. |

| `CRYO_OIDC_CLIENT_ID` | `unset` | **Set in production.** OIDC OAuth client id. |

| `CRYO_OIDC_CLIENT_SECRET` | `unset` | **Set in production.** OIDC client secret for authorization-code exchange. **Security:** Keep in the Kubernetes Secret. |

| `CRYO_OIDC_ISSUER_URL` | `unset` | **Set in production.** OIDC issuer URL (production SSO). Enables RS256 IdP token verification. |

| `CRYO_OIDC_JWKS_URI` | `unset` | Optional JWKS URI override (defaults to issuer/.well-known/jwks.json). |

| `CRYO_OIDC_REDIRECT_URI` | `unset` | **Set in production.** OAuth redirect URI registered with the IdP (UI callback). |

### CRYO_OTEL_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing and OTLP export. |

| `CRYO_OTEL_ENDPOINT` | `"http://localhost:4318/v1/traces"` | OTLP HTTP endpoint for trace export. |

| `CRYO_OTEL_SAMPLE_RATE` | `1.0` | Trace sampling rate (0.0-1.0). |

| `CRYO_OTEL_SERVICE_NAME` | `"cryo"` | Service name attached to exported spans. |

### CRYO_QPP_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_QPP_ENABLED` | `false` | When true, the Governor calls QPPModel.predict() before admitting a maintenance job and refuses jobs whose predicted p95 exceeds the per-destination target. |

| `CRYO_QPP_MIN_OBSERVATIONS` | `20` | Minimum observations before QPPModel trusts the learned regression. |

| `CRYO_QPP_TARGET_P95_MS` | `600000` | Default per-destination QPP p95 target in milliseconds. |

### CRYO_QUERY_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_QUERY_COST_REPORT_ENABLED` | `false` | Post the Trino query-cost report to Slack. |

| `CRYO_QUERY_GETS_PER_FILE` | `1.0` | *Not described yet.* |

| `CRYO_QUERY_LOG_ICEBERG_MAX_ROWS_PER_WINDOW` | `25000` | Last-resort per-window row cap for the iceberg_table read-through query-log path. Only triggers if a single window still has an extreme row count; excess rows in that window are dropped (logged as a warning) rather than blocking the watermark from advancing past that window. |

| `CRYO_QUERY_LOG_ICEBERG_MAX_WINDOWS_PER_TICK` | `4` | Max number of query_log_iceberg_window_hours windows processed per aggregation call. Bounds how much a large backlog (first-ever run, or catch-up after an outage) can do in one tick; the rest is picked up on the next scheduled tick. |

| `CRYO_QUERY_LOG_ICEBERG_WINDOW_HOURS` | `1` | Time-window size for the iceberg_table read-through query-log path (cryo/telemetry/iceberg_source.py). |

| `CRYO_QUERY_LOG_RETENTION_DAYS` | `90` | Days of raw query_logs rows to keep before the scheduler purges them. query_patterns (the aggregated signal partition/layout recommendations actually consume) is unaffected by purging, since it&#x27;s aggregated before rows age out. |

### CRYO_REPORT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_REPORT_COST_DOD_DECLINE_PCT` | `30.0` | Day-over-day fleet cost decrease (%) that flags a decline worth noting. |

| `CRYO_REPORT_COST_DOD_SPIKE_PCT` | `10.0` | Day-over-day fleet cost increase (%) that flags a spike. |

| `CRYO_REPORT_COST_WOW_DECLINE_PCT` | `35.0` | Week-over-week fleet cost decrease (%) that flags a decline worth noting. |

| `CRYO_REPORT_COST_WOW_SPIKE_PCT` | `15.0` | Week-over-week fleet cost increase (%) that flags a spike. |

| `CRYO_REPORT_EMAIL_RECIPIENTS` | `[]` | Email recipients for daily anomaly reports. |

| `CRYO_REPORT_IMPACT_DOD_MOVE_PCT` | `25.0` | Day-over-day change (%) in GB reclaimed that&#x27;s worth flagging either way. |

| `CRYO_REPORT_IMPACT_WOW_MOVE_PCT` | `20.0` | Week-over-week change (%) in GB reclaimed that&#x27;s worth flagging either way. |

| `CRYO_REPORT_QUERY_COST_DOD_DECLINE_PCT` | `30.0` | Day-over-day Trino query cost decrease (%) that flags a decline worth noting. |

| `CRYO_REPORT_QUERY_COST_DOD_SPIKE_PCT` | `15.0` | Day-over-day Trino query cost increase (%) that flags a spike. |

| `CRYO_REPORT_QUERY_COST_WOW_DECLINE_PCT` | `35.0` | Week-over-week Trino query cost decrease (%) that flags a decline. |

| `CRYO_REPORT_QUERY_COST_WOW_SPIKE_PCT` | `20.0` | Week-over-week Trino query cost increase (%) that flags a spike. |

### CRYO_ROI_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_ROI_GATE_ENABLED` | `false` | Enable ROI-based governor gate for maintenance admission decisions. |

| `CRYO_ROI_GATE_SHADOW_MODE` | `true` | Log ROI decisions without enforcing skip/defer while tuning thresholds. |

| `CRYO_ROI_MIN_BCR` | `1.0` | Minimum benefit-cost ratio required for non-streaming tables. |

| `CRYO_ROI_MIN_BCR_STREAMING` | `0.5` | Minimum benefit-cost ratio required for streaming/hot tables. |

| `CRYO_ROI_SHADOW_MIN_SAMPLES` | `200` | Minimum shadow decisions before suggesting ROI enforcement. |

### CRYO_S3_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_S3_GET_USD_PER_1K` | `0.0004` | *Not described yet.* |

| `CRYO_S3_LIST_USD_PER_1K` | `0.005` | *Not described yet.* |

| `CRYO_S3_LOG_INGEST_CONCURRENCY` | `16` | Max concurrent S3 GetObject calls when reading log objects for a single bucket&#x27;s ingestion run -- I/O-bound work, so higher than a typical CPU-bound worker pool. |

| `CRYO_S3_LOG_INGEST_INTERVAL_HOURS` | `24` | Hours between S3 request-cost ingestion runs (docs/s3-request-cost-tracking.md §10) -- reads and aggregates every enabled bucket&#x27;s log objects natively (no Trino/Hive-connector dependency). |

| `CRYO_S3_LOG_INGEST_MAX_OBJECTS_PER_DAY` | `20000` | Safety cap on how many S3 access-log objects are read per date-partition per ingestion run. A busy bucket can deliver many small per-flush log files; this bounds S3 API call volume per run rather than unbounded object-count growth. Days that hit the cap are logged as a lower-bound (not exact) count, not an error. |

| `CRYO_S3_LOG_TRINO_CATALOG` | `"hive"` | Trino Hive-connector catalog/schema used only to clean up an external log table from an older Trino-based ingestion run when tearing down S3 request-cost tracking for a bucket (cryo/integrations/s3_bucket_provisioning.py). Ingestion itself reads and aggregates S3 access logs natively -- no Trino dependency. |

| `CRYO_S3_LOG_TRINO_SCHEMA` | `"cryo_s3_logs"` | Schema within s3_log_trino_catalog for the legacy teardown path above. |

| `CRYO_S3_PUT_USD_PER_1K` | `0.005` | *Not described yet.* |

| `CRYO_S3_REQUEST_COST_RETENTION_DAYS` | `35` | Days of S3RequestCostDaily rows kept before the retention job prunes them -- a few days past the 30-day TableCost rollup window. |

### CRYO_SCAN_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_SCAN_DEAD_TABLE_FAILURE_THRESHOLD` | `10` | consecutive_scan_failures at or above this AND zero query_logs rows in scan_dead_table_lookback_days -> auto-deactivate the table. |

| `CRYO_SCAN_DEAD_TABLE_LOOKBACK_DAYS` | `30` | *Not described yet.* |

| `CRYO_SCAN_ENGINE` | `"pyiceberg"` | Metadata walk implementation for current-snapshot stats. &#x27;go&#x27; shells out to cryo-engine-go scan-stats (streaming manifests); &#x27;pyiceberg&#x27; uses the in-process path. |

| `CRYO_SCAN_FRESHNESS_EXCLUDED_SCHEMAS` | `"temp"` | Comma-separated schema names excluded from scan_freshness/scan_failures (health checks) -- for scratch/staging schemas whose tables are never queried, so a slow scan cadence against them isn&#x27;t a real signal. |

| `CRYO_SCAN_GO_BINARY` | `unset` | Optional path to cryo-engine-go; otherwise resolve like native runner. |

| `CRYO_SCAN_NEEDS_COMPACTION_THRESHOLD` | `128` | *Not described yet.* |

| `CRYO_SCAN_NUM_CHUNKS` | `4` | Number of chunks a fleet scan is split into. |

| `CRYO_SCAN_PARALLEL_MANIFESTS` | `4` | Max threads for parallel manifest fetching within a table. Kept modest under typical 2Gi scanner-worker limits: each in-flight manifest still materializes its entry list. |

| `CRYO_SCAN_PARALLEL_TABLES` | `0` | Tables to scan in parallel per chunk (0=serial). |

| `CRYO_SCAN_SMALL_FILE_MB` | `89.6` | *Not described yet.* |

| `CRYO_SCAN_TABLE_TIMEOUT_SECONDS` | `180.0` | Hard wall-clock bound for scanning a single table (catalog load, stats analysis, and orphan-file listing). |

### CRYO_SILENT_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_SILENT_LOSS_BATCH_SIZE` | `500` | Tables swept per scheduler tick by the silent-data-loss sweep, not the whole fleet at once -- sweeping ~13k tables unbounded in one session accumulated every Table/TableStats ORM object simultaneously and OOMKilled cryo-scheduler every ~5min in production. |

| `CRYO_SILENT_LOSS_CHECK_INTERVAL_MIN` | `5` | Minutes between silent-data-loss scans per table (CRYO_SILENT_LOSS_CHECK_INTERVAL_MIN). |

| `CRYO_SILENT_LOSS_LOCAL_ROOT` | `".local/tables"` | Local filesystem root for table data in dev (CRYO_SILENT_LOSS_LOCAL_ROOT). |

### CRYO_SLACK_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_SLACK_CHANNEL` | `unset` | Override channel for Slack notifications (optional). |

| `CRYO_SLACK_DAILY_REPORT_CRON` | `"0 9 * * *"` | Cron expression for daily anomaly report (default: 9 AM daily). |

| `CRYO_SLACK_DAILY_REPORT_ENABLED` | `false` | Enable daily anomaly report to Slack. |

| `CRYO_SLACK_SIGNING_SECRET` | `unset` | Signing secret for Slack interactive requests. Unset rejects those requests. |

| `CRYO_SLACK_WEBHOOK_URL` | `unset` | Slack incoming webhook URL for daily reports. |

### CRYO_SMTP_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_SMTP_FROM` | `unset` | From address for report emails. |

| `CRYO_SMTP_HOST` | `unset` | SMTP host for email notifications. |

| `CRYO_SMTP_PASSWORD` | `unset` | SMTP password. **Security:** Keep in the Kubernetes Secret. |

| `CRYO_SMTP_PORT` | `587` | SMTP server port for email notifications. |

| `CRYO_SMTP_USER` | `unset` | SMTP user name. |

| `CRYO_SMTP_USE_TLS` | `true` | Use STARTTLS for SMTP. |

### CRYO_TABLE_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_TABLE_ALERT_COOLDOWN_HOURS` | `24` | Minimum hours between repeat breach alerts for the same table. |

| `CRYO_TABLE_ALERT_DISPATCH_INTERVAL_HOURS` | `1` | Hours between SLA/data-quality breach checks for owned/subscribed tables. |

| `CRYO_TABLE_STATS_HISTORY_PRUNE_BATCH_SIZE` | `10000` | Maximum table_stats_history rows deleted in one transaction. |

| `CRYO_TABLE_STATS_HISTORY_PRUNE_MAX_ROWS_PER_TICK` | `100000` | Maximum table_stats_history rows deleted by one daily retention tick. |

| `CRYO_TABLE_STATS_HISTORY_RETENTION_DAYS` | `180` | Days to retain per-scan TableStatsHistory snapshots. |

### CRYO_TELEMETRY_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_TELEMETRY_ATHENA_WORKGROUP` | `unset` | Optional Athena workgroup filter for query-log ingestion. |

| `CRYO_TELEMETRY_DUCKDB_DATABASE` | `unset` | DuckDB database path for query_history / duckdb_logs ingestion. |

| `CRYO_TELEMETRY_DUCKDB_ENABLED` | `false` | Enable DuckDB query-log ingestion (dev-only). |

| `CRYO_TELEMETRY_INGEST_ON_STARTUP` | `true` | When true, scheduler attempts query-log ingest ticks if configured. |

| `CRYO_TELEMETRY_TRINO_PASSWORD` | `unset` | Password for telemetry_trino_url, if the coordinator requires auth (CRYO_TELEMETRY_TRINO_PASSWORD). Unset means no BasicAuthentication is attempted. |

| `CRYO_TELEMETRY_TRINO_SSL_VERIFY` | `true` | Verify TLS certificates when connecting to telemetry_trino_url (CRYO_TELEMETRY_TRINO_SSL_VERIFY). Only disable for coordinators with self-signed/internal-CA certs you&#x27;ve already verified out of band. |

| `CRYO_TELEMETRY_TRINO_URL` | `unset` | Trino coordinator URL for scheduled query-log ingestion (CRYO_TELEMETRY_TRINO_URL). |

| `CRYO_TELEMETRY_TRINO_USER` | `unset` | Username for telemetry_trino_url, if the coordinator requires auth (CRYO_TELEMETRY_TRINO_USER). Defaults to &#x27;cryo&#x27; if a password is set but no username is given. |

### CRYO_TRINO_*

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_TRINO_COST_PRICING_ENABLED` | `false` | Enables deriving a real $/GiB Trino compute price from the trino-db namespace&#x27;s actual Grafana Athena CUR cost (cryo.reporting.cost_ceiling.fetch_namespace_cost_range) divided by real bytes scanned that day (QueryCostAttributionDaily&#x27;s rollup), and writing it onto QueryCostAttributionDaily.estimated_scan_cost_usd --… |

| `CRYO_TRINO_COST_PRICING_LOOKBACK_DAYS` | `10` | How many trailing days to (re-)price on each tick. Wider than CUR&#x27;s 24-48h lag on purpose, so a day whose cost data was still incomplete on a prior tick gets priced once it catches up, not stuck NULL forever. |

| `CRYO_TRINO_COST_PRICING_NAMESPACE` | `"trino-db"` | Kubernetes namespace to price against (see trino_cost_pricing_enabled). |

| `CRYO_TRINO_HOT_PRESET_NAME` | `"daily-trino-hot-tables"` | Name of the Preset (see scripts/seed_trino_hot_preset.py) that TrinoHotPresetRunner repins daily -- must match an existing, seeded Preset.name. |

| `CRYO_TRINO_HOT_PRESET_REPIN_INTERVAL_HOURS` | `24` | Hours between repins of the daily top-N-by-Trino-query-frequency maintenance preset (cryo.scheduler.trino_hot_preset.repin_trino_hot_tables) -- daily by default, matching its purpose: concentrate maintenance effort on whichever tables are actually driving Trino scan cost right now, re-evaluated once a day. |

| `CRYO_TRINO_HOT_PRESET_TABLE_LIMIT` | `500` | How many top-Trino-query-frequency tables to pin to the preset each repin. |

### Other

| Variable | Default | What it does |
| --- | --- | --- |

| `CRYO_ALLOW_DEV_TOKEN_IN_PROD` | `false` | Explicit opt-in to keep POST /api/v1/auth/token (self-declared role, no real identity check) enabled even when environment==&#x27;prod&#x27;. Only for bridging the gap before OIDC is configured -- anyone who can reach the API can self-grant any role while this is true. Turn off once oidc_issuer_url is set. **Security:** Re-opens the unauthenticated dev token route. Leave unset. |

| `CRYO_API_RATE_LIMIT_BURST` | `10` | Burst capacity for expensive API endpoint rate limiting. |

| `CRYO_API_RATE_LIMIT_RPS` | `0.5` | Token refill rate for expensive API endpoints (requests/sec). |

| `CRYO_APP_NAME` | `"Cryo"` | Display name used in API metadata. |

| `CRYO_BENEFIT_SCORE_SWEEP_VIA_K8S_JOBS` | `false` | Recovery plan §2/§8, 2026-09-20: when true, BenefitScoreUpdaterRunner&#x27;s tick ensures a durable analysis_tasks row is due and dispatches a bounded K8s Job (cryo.analysis.dispatch) to process it, instead of running the multi-hour sweep in-process (the scheduler-OOM root cause this whole mechanism replaces). |

| `CRYO_BENEFIT_SCORE_UPDATE_INTERVAL_HOURS` | `6` | Hours between AutoComp benefit score recalculations. |

| `CRYO_COMMIT_RACE_ALERT_LOOKBACK_HOURS` | `24` | Window scanned for native compaction runs that lost the optimistic-concurrency commit race (audit detail tagged &#x27;commit_race_lost&#x27;); see docs/native-engine-scoping-research.md §3.2. |

| `CRYO_COMMIT_RACE_ALERT_MIN_OCCURRENCES` | `2` | Number of commit-race losses within the lookback window before a table is flagged as &#x27;compaction losing the commit race&#x27;. 1 = alert on the first loss. |

| `CRYO_COMPUTE_STATISTICS_ENABLED` | `true` | Enable Puffin statistics computation after compaction by default. |

| `CRYO_CORS_ALLOW_ORIGINS` | `["http://localhost:5173"]` | Origins allowed to call the API from a browser, as a JSON list. Not needed when the UI is served through the web pod&#x27;s /api proxy. **Security:** Never list *. |

| `CRYO_CREDENTIAL_VENDING_ENABLED` | `false` | Enable REST catalog STS credential vending in worker. |

| `CRYO_DATABASE_URL` | `"postgresql+psycopg://cryo:cryo@localhost:5432/cryo"` | **Set in production.** SQLAlchemy URL of the PostgreSQL database that holds the cryo schema. **Security:** Contains the database password. Keep in the Kubernetes Secret. |

| `CRYO_DISPATCH_HEARTBEAT_STALL_MINUTES` | `1560` | Minutes since the last MaintenanceJob admitted/started/finished before vitals.dispatch_heartbeat calls dispatch stalled. |

| `CRYO_DURABLE_INTENTS_ENABLED` | `false` | Bind new maintenance jobs to immutable plans, monotonic table fences, attempts, and effect receipts. Safety-alpha migration flag; legacy jobs remain executable. |

| `CRYO_DURABLE_INTENT_TTL_HOURS` | `24` | Maximum time a bound maintenance intent remains executable. Scheduled intents start their TTL at scheduled dispatch time and are also capped by the run deadline. |

| `CRYO_ENFORCE_TENANT_ISOLATION` | `false` | When true, every JWT must carry tenant_id and queries are scoped. |

| `CRYO_ENGINE_CONNECT_TIMEOUT_SEC` | `5.0` | Per-request connect timeout (seconds) for Trino coordinator probes and statement execution. The trino client doesn&#x27;t expose a connect timeout directly, so we patch the requests Session it uses. |

| `CRYO_ENVIRONMENT` | `"dev"` | **Set in production.** Deployment environment: dev, test, staging or prod. prod turns off the dev token route, and the Helm chart requires every enabled image to be pinned by digest. **Security:** Set prod for any install other people can reach. |

| `CRYO_EVOLVING_CATALOG_CLEANUP_INTERVAL_HOURS` | `6` | Hours between sweeps deactivating orphaned &#x27;*_evolving&#x27; catalog entries (cryo.db.repositories.sources.deactivate_orphaned_evolving_catalog_entries) -- the fix existed since 2026-08-26 (ace7f4b) but was never wired to a scheduled runner, so the same historical rename artifact (1,200+ tables, location IS NULL, a… |

| `CRYO_FAST_PATH_CHECK_ENABLED` | `true` | Cheap new-table health sweep on the telemetry tick (RFC Features 2+3). |

| `CRYO_FAST_PATH_CHECK_MAX_TABLES_PER_TICK` | `50` | Cap on how many newly discovered tables the fast-path sweep inspects per telemetry tick when fast_path_check_enabled is true. |

| `CRYO_FLEET_ADMISSION_RATE_CAP` | `500` | Blunt, mechanism-agnostic ceiling on total jobs (any status, any destination, fleet-wide) admitted within fleet_admission_rate_window_minutes. |

| `CRYO_FLEET_ADMISSION_RATE_WINDOW_MINUTES` | `15` | Rolling window used by fleet_admission_rate_cap -- see that field&#x27;s docstring for the incident this pair guards against. |

| `CRYO_FLINK_ENGINE_ENABLED` | `false` | Enable Flink config-generation engine (does not submit jobs). |

| `CRYO_FORECAST_DEFAULT_HORIZON_HOURS` | `24` | Default workload forecast horizon for smart-maintenance API endpoints. |

| `CRYO_FORECAST_WINDOW_DAYS` | `14` | Historical window used by EWMA workload forecaster. |

| `CRYO_GOVERNOR_BANDIT_TUNING_ENABLED` | `false` | When true, a Thompson-sampling bandit may override the admission config an operator set on a destination (per_replica_cap, high_load_ratio/fraction, default_cooldown_hours, min_benefit_score) and learning signals are recorded on every admission and deferral. |

| `CRYO_GOVERNOR_SHADOW_SAMPLE_RATE` | `20` | Evaluate the ROI-gate and resource-predictor shadow paths for 1 in N candidates per wave, not every one. |

| `CRYO_GRAFANA_ATHENA_DATASOURCE_UID` | `""` | Grafana datasource uid for the Athena CUR connection used by the cost ceiling. Required when cost_ceiling_enabled or Trino scan-cost pricing is on. |

| `CRYO_GRAFANA_BASE_URL` | `""` | Base URL for cost_ceiling&#x27;s Grafana API calls, no trailing slash. Required when cost_ceiling_enabled is true. |

| `CRYO_HEALTH_PORT` | `8081` | HTTP port for /healthz and /metrics on background services. |

| `CRYO_HILBERT_CLUSTERING_ENABLED` | `false` | Allow Hilbert-curve clustering. Off by default: Iceberg only gained hilbert() in 1.12.0 on the Spark 4.1 path (PR #16827, merged 2026-08-25), and an unsupported runtime fails at procedure-parse time after the job is submitted. Requests downgrade to Z-order unless the versions below confirm support. |

| `CRYO_JWT_SECRET` | `unset` | **Set in production.** HS256 secret for API JWTs. Defaults to secret_key when unset. **Security:** Set a separate random value so token signing can be rotated on its own. |

| `CRYO_MAX_CONCURRENT_JOBS_PER_METASTORE` | `150` | Cross-destination cap on RUNNING jobs whose table&#x27;s source shares the same Hive metastore URI, checked at native-engine admission time. |

| `CRYO_MAX_JOBS_PER_SCHEDULED_RUN` | `500` | Hard cap on how many tables one cron-triggered run may target. build_scheduled_run has always accepted a budget_hint, but the cron path never passed one, so a single preset fired against every matching table in the fleet. |

| `CRYO_MCP_RUN_PRESET_ENABLED` | `false` | Allow write-capable MCP run_preset tool. Disabled by default for safety. |

| `CRYO_ML_DRIFT_CHECK_INTERVAL_HOURS` | `24` | Hours between scheduled health-classifier drift evaluations. |

| `CRYO_NATIVE_ENGINE_ENABLED` | `true` | Enable native cryo-engine maintenance (default workflow). |

| `CRYO_OBSERVATION_LAKEHOUSE_PATH` | `".local/observations"` | On-disk path where the observation lakehouse stores long-term actuations. |

| `CRYO_OFFPEAK_END_IST` | `0` | End hour (IST) of the off-peak window. Equal start and end disables the gate. |

| `CRYO_OFFPEAK_START_IST` | `0` | Start hour (IST) of the off-peak window. Equal start and end disables the gate. |

| `CRYO_PAGERDUTY_ROUTING_KEY` | `unset` | PagerDuty Events API v2 routing key for high-severity anomalies. |

| `CRYO_PLAN_VERIFIER_ENABLED` | `true` | When false, /plans/verify and the enqueue-time verifier pass-through return 503. Lets ops kill the feature without redeploying. |

| `CRYO_PRODUCTION_HEALTH_CHECK_ENABLED` | `false` | Post the production health check to Slack. |

| `CRYO_PROPERTY_AUTOPILOT_ENABLED` | `false` | Off by default -- opt-in. |

| `CRYO_PROPERTY_AUTOPILOT_MAX_TABLES_PER_TICK` | `100` | Cap on how many tables get their safe property recommendations applied per telemetry tick when property_autopilot_enabled is true -- same rationale as layout_recommendation_max_tables_per_tick above: bounds the cost of a single tick rather than trying to sweep the whole fleet in one pass. |

| `CRYO_PUFFIN_LOCAL_DIR` | `".local/puffin"` | On-disk directory for Puffin blobs in local dev (CRYO_PUFFIN_LOCAL_DIR). |

| `CRYO_REDIS_URL` | `"redis://localhost:6379/0"` | **Set in production.** URL of the Redis used for the job queue, leases and short-lived markers. **Security:** Keep in the Kubernetes Secret if it carries a password. |

| `CRYO_SCANNER_MAX_PARTITIONS_PER_TABLE` | `5000` | Cap on how many partition rows a single scan persists to table_partitions per table, keeping the ones with the most small files (the actual compaction signal). |

| `CRYO_SCANNER_WAIR_FILE_SAMPLE_SIZE` | `1000` | Maximum deterministic current-snapshot data-file bound samples retained per table for WAIR analysis, restricted to range-queried numeric/temporal columns. Zero disables collection. |

| `CRYO_SCRIPT_RUNTIME` | `"docker"` | Container runtime for custom maintenance scripts (runsc=gVisor). |

| `CRYO_SECRET_KEY` | `"dev-only-change-me"` | **Set in production.** Signs Cryo&#x27;s HS256 tokens when CRYO_JWT_SECRET is unset, and derives the key that encrypts stored connection secrets. **Security:** The default is published. Set a random value of at least 32 characters, and back it up: changing it makes stored secrets unreadable. |

| `CRYO_SHARED_S3_REPORT_ENABLED` | `false` | Post the shared-S3-path anomaly report to Slack. |

| `CRYO_SPARK_ICEBERG_VERSION` | `unset` | Iceberg runtime version on the Spark destination, e.g. &#x27;1.12.0&#x27;. Used to gate Hilbert clustering; unknown is treated as unsupported, never as new enough. |

| `CRYO_SPARK_RUNTIME_VERSION` | `unset` | Spark version on the destination, e.g. &#x27;4.1.0&#x27;. Used to gate Hilbert clustering; unknown is treated as unsupported. |

| `CRYO_STATS_COLUMNS_LIMIT` | `50` | Max columns to compute stats for (high-cardinality priority). |

| `CRYO_STUCK_ADMITTED_AFTER_SECONDS` | `900` | Grace period before reconcile_stuck_admitted_jobs treats an ADMITTED job with no engine_ref as undispatched. Only applies to jobs that are genuinely absent from the claim queue; a job still waiting its turn is left alone however long the queue is. |

| `CRYO_UNMATCHED_TABLES_CHECK_INTERVAL_HOURS` | `6` | Hours between fleet-wide sweeps for active tables that resolve to no effective preset (cryo.scheduler.rules.effective_preset returns None) -- these are never scheduled for maintenance by any cron and previously went undetected. |

| `CRYO_WAVE_WAIT_SEC` | `300` | *Not described yet.* |

| `CRYO_WEB_APP_BASE_URL` | `unset` | Base URL of the Cryo web app, used to build deep links in breach alerts. |

| `CRYO_WORKER_DISPATCH_BACKLOG_PER_REPLICA` | `3` | Admission backpressure: the governor will not grow the undispatched ADMITTED backlog beyond this many jobs per live worker replica. |

### Read outside Settings

These are read with `os.environ` by one module rather than through `Settings`. Most are set by Cryo itself on the Jobs it creates.

| Variable | What it does |
| --- | --- |

| `CRYO_ANALYSIS_IMAGE` | Image for analysis Jobs, when the caller passes none. |

| `CRYO_ANALYSIS_IMAGE_PULL_POLICY` | Pull policy for analysis Jobs. Default IfNotPresent. |

| `CRYO_ANALYSIS_SERVICE_ACCOUNT` | ServiceAccount for analysis Jobs. Defaults to default. |

| `CRYO_BACKUP_KEY` | Required by python -m cryo.operations.backup: 32 random bytes, URL-safe base64. Encrypts and decrypts backups. Store it apart from the backups. |

| `CRYO_ENGINE_BINARY` | Path to the native engine binary, overriding the one on PATH. |

| `CRYO_GRAFANA_TOKEN` | Grafana API token for the daily and health-check reports. |

| `CRYO_K8S_NAMESPACE` | Namespace for native Jobs when POD_NAMESPACE is unset. |

| `CRYO_NATIVE_IMAGE` | Image for native and analysis Jobs, when the destination sets none. |

| `CRYO_NATIVE_IMAGE_PULL_POLICY` | Pull policy for native Jobs, when the destination sets none. |

| `CRYO_NATIVE_IMAGE_PULL_SECRETS` | Pull secrets for native Jobs, when the destination sets none. |

| `CRYO_NATIVE_SERVICE_ACCOUNT` | ServiceAccount for native Jobs, when the destination sets none. |

| `CRYO_NATIVE_TTL_SECONDS` | ttlSecondsAfterFinished for native Jobs, when the destination sets none. |

| `CRYO_PAYLOAD_B64` | Internal. Job payload passed by Cryo to its own Job pods. |

| `CRYO_PAYLOAD_FILE` | Internal. Path of the Job payload Cryo mounts into its own Job pods. |

| `CRYO_SCANNER_JOB_DISPATCH_BATCH_SIZE` | Tables per scanner Job when scans dispatch as Jobs. Default 1. |

| `CRYO_SPARK_DRIVER_POD_CLEANUP_TTL_SECONDS` | Age after which finished Spark driver pods are deleted. Default 3600. |

| `CRYO_SPARK_K8S_SLOT_CAP` | Override for the Spark-on-Kubernetes slot cap. |

| `CRYO_STATE_DIR` | Directory for node-local state files. Default /tmp/cryo. |

## Helm values

The top-level values in `deploy/helm/cryo/values.yaml`, and the keys under each one. The comments in `values.yaml` explain every key and why its default is what it is. `values.schema.json` validates the types of some of them.

| Value | What it configures | Keys |
| --- | --- | --- |

| `image` | The Cryo image for the API, workers, governor, scheduler, scanner and CronJobs. | `repository`, `tag`, `digest`, `pullPolicy` |

| `imagePullSecrets` | Pull secrets added to every pod. | — |

| `serviceAccount` | The ServiceAccount Cryo&#x27;s pods run as. Annotate it for IRSA. | `create`, `name`, `annotations` |

| `podSecurityContext` | Pod security context applied to every Cryo pod. | `runAsNonRoot`, `runAsUser`, `runAsGroup`, `fsGroup`, `seccompProfile` |

| `containerSecurityContext` | Container security context applied to every Cryo container. | `allowPrivilegeEscalation`, `privileged`, `capabilities` |

| `global` | Environment for every process: env (plain values), envFromSecret and envFromConfigMap. | `env`, `envFromSecret`, `envFromConfigMap` |

| `observability` | Prometheus scrape annotations and the metrics port. | `prometheusScrape`, `metricsPort` |

| `api` | The API Deployment: replicas, Uvicorn workers, probes, resources and Ingress annotations. | `replicas`, `port`, `workers`, `probe`, `ingressAnnotations`, `resources` |

| `worker` | The maintenance worker Deployment. | `replicas`, `resources` |

| `governor` | The admission governor Deployment (leader-elected). | `replicas`, `probe`, `resources` |

| `scheduler` | The cron scheduler Deployment (leader-elected). | `replicas`, `resources` |

| `scannerWorker` | The ad-hoc scan consumer Deployment and its HPA or KEDA autoscaling. | `replicas`, `resources`, `autoscaling` |

| `scanner` | The fleet-scan CronJob: schedule, chunks, and Job dispatch. | `schedule`, `suspend`, `chunks`, `resources`, `jobDispatch` |

| `maintenance` | The nightly batch-maintenance CronJob and its Spark lanes. Off by default. | `enabled`, `schedule`, `suspend`, `topN`, `maxLanes`, `laneMaxCpuMilli`, `laneMaxMemMib`, `laneMaxNodeCpu`, `laneCapacityTypes`, `maxRuntimeMinutes`, `cooldownHours`, `resources` |

| `backup` | The encrypted offsite backup CronJob. Off by default. | `enabled`, `schedule`, `s3Uri`, `retentionDays`, `backoffLimit`, `serviceAccount`, `resources` |

| `native` | Native engine Jobs, their RBAC, KEDA and Karpenter settings. Off by default. | `enabled`, `image`, `serviceAccount`, `ttlSecondsAfterFinished`, `maxCpu`, `maxMemory`, `spot`, `keda`, `karpenter` |

| `analysis` | Analysis-runner RBAC for scheduler-dispatched analysis Jobs. Off by default. | `enabled` |

| `spark` | Cryo-managed Spark on Kubernetes (spark.k8s). Off by default. | `k8s` |

| `redis` | A single in-namespace Redis with no volume. Set CRYO_REDIS_URL yourself. | `enabled`, `image`, `port`, `probe`, `resources`, `maxmemory`, `maxmemoryPolicy`, `save`, `podSecurityContext` |

| `migration` | The pre-install/pre-upgrade migration Job image and retries. | `image`, `backoffLimit` |

| `web` | The UI Deployment and Service. Off by default. | `enabled`, `replicas`, `image`, `port`, `probe`, `service`, `podSecurityContext`, `resources` |

| `service` | The API Service type and port. | `type`, `port` |

## Keeping this page current

Both tables come from `python -m cryo.config_reference`, which reads `cryo.config.Settings`, scans the package for other `CRYO_*` reads, and parses `values.yaml`. CI runs it with `--check`. It fails when the committed tables are stale, when code reads a new variable outside `Settings` without a description, when a new `Settings` field has no description, or when a new top-level Helm value has none. After changing configuration, run:

**terminal**

```
$ make config-docs
```

## Where to go next

SecurityWhat the security-relevant settings protect.Install on KubernetesWhere each kind of setting goes in a Helm install.

Source: https://tech-pw.github.io/cryo/configuration/
Markdown: https://tech-pw.github.io/cryo/configuration/index.md
