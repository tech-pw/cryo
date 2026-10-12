# Security scan triage — 12 Oct 2026

InfoSec ran PW-Security-Lens 2.0.0 against `cryo-main.zip` (scan reference `2afb0354830f`):
a secure code review (SCR, 4,463 findings) and a software composition analysis (SCA,
320 findings). This page records what was real, what was fixed, and why the rest was
accepted, so the next scan can be triaged against it.

## What changed

### Containers run as non-root

| Image | User | Notes |
|---|---|---|
| `Dockerfile` (api/worker/governor/scheduler/scanner/migrate/backup, native Jobs) | `cryo` 10001:10001 | `/app/.local` is writable for the relative `puffin` / observation-lakehouse defaults. State files already go to `/tmp/cryo`. |
| `web/Dockerfile.prod` | `nginx` 101 | Base is now `nginxinc/nginx-unprivileged`, which listens on **8080** (`web/nginx.conf`, Helm `web.port`). |
| `web/Dockerfile` (dev) | `node` | |
| `docker/spark-runner/Dockerfile` (unused) | 10001 | |
| `deploy/hive/Dockerfile` (dev only) | `hive` | Compose still sets `user: root` for this service. |
| `Dockerfile.spark` | 185 | Unchanged; already non-root. |

### Helm pod/container security contexts

`values.yaml` now has `podSecurityContext` (runAsNonRoot, UID/GID/fsGroup 10001, RuntimeDefault
seccomp) and `containerSecurityContext` (no privilege escalation, not privileged, drop ALL
capabilities). Every Deployment/Job/CronJob includes them via `cryo.podSecurityContext` and
`cryo.containerSecurityContext`. `redis.podSecurityContext` (999/1000) and
`web.podSecurityContext` (101) override the UID to match those images' own users.

`fsGroup` is load-bearing on EKS: the projected IRSA web-identity token is root-only
unless the pod sets one.

`readOnlyRootFilesystem` is **not** enabled yet. Python, boto, and kube clients write to
`$HOME`/caches, so turning it on needs emptyDir mounts for `/tmp`, `/app/.local`, and
`/home/cryo`, plus a live validation pass.

Jobs Cryo creates at runtime (native engine, Spark submit, maintenance lanes, analysis) get
the same container hardening from `cryo/engines/k8s_security.py`. They deliberately don't
set `runAsNonRoot` or a UID: a separately pinned engine image that still runs as root would
otherwise fail to start. These images now default to non-root anyway.

### Code

- `engine-go/internal/predicate`: an identity predicate value outside int32 range for an
  `int` column now returns an error. Previously it silently wrapped and could filter a
  different partition.
- `engine-go/cmd/cryo-engine-go`: pprof profile directory and files are created 0700/0600.
- Trino identifiers built from catalog metadata are quoted and escaped through
  `cryo.engines.trino.sql.quote_identifier` / `quote_qualified_name` (dedup sampling and
  verification, union-search SQL suggestions, S3 access-log table names).
- `cryo/telemetry/ingestion.py`: config-supplied catalog, schema, and query-log table names
  must be plain dotted identifiers.
- `cryo/security/google_id_token.py`: the ID token is URL-encoded before being sent to
  Google's tokeninfo endpoint.
- `cryo/integrations/alerts.py`: webhook URLs must be http(s). `urlopen` would otherwise
  accept `file://`.
- `web/nginx.conf`: only `Upgrade: websocket` is forwarded to the API, which closes H2C
  request smuggling. Also sets `server_tokens off`.

### CI / supply chain

- Every GitHub Action is pinned to a full commit SHA (version in a trailing comment).
- `ci.yml`, `canary.yml`, and `e2e-nightly.yml` have top-level `permissions: contents: read`.
- Dependabot uses a 7-day cooldown on every ecosystem.

### Dependencies

- Python (`uv.lock` + `requirements.lock`): pyjwt 2.15.1, anyio 4.15.1, aiohttp 3.14.5,
  cryptography 50.0.2, urllib3 2.8.0, pyasn1 0.6.4, thrift 0.25.0, mako 1.4.3,
  multidict 6.9.1, oauthlib 4.0.0. The `pyproject.toml` floors the scanner read as
  "installed" versions are raised (pyjwt ≥2.15, cryptography ≥50, pyarrow ≥23.0.1).
  The notebook `requirements.txt` floors are raised too.
- Go (`engine-go/go.mod`): x/crypto 0.57, x/net 0.60, x/sys 0.48, x/text 0.42,
  otel 1.45, klauspost/compress 1.18.7, apache/thrift 0.24.
- npm (`web/package-lock.json`): every flagged package is at its fixed version, and
  `npm audit` is clean.

## Accepted / false positives

- **3,903 "use of assert"** findings are pytest assertions. Hardcoded-password findings
  under `tests/` are fixtures.
- **Subprocess findings** all use argv lists with no shell. `destination.config.binary_path`
  is admin-only (`require_admin` on destination writes).
- **"Formatted SQL"** in `engines/trino/engine.py` uses the renderer's already-quoted
  `table_ref`. `query_status.py` allow-lists the query id. Migrations interpolate module
  constants only.
- **Go integer-conversion findings** in `internal/ops` and `progress` convert `len()`,
  byte sizes, or 0–100 percentages into unsigned telemetry counters.
  `third_party/iceberg-go-*-patched` is vendored upstream code and is not modified here.
- **`docker-compose*.yml` credentials** are local-dev only (see
  `LOCAL_DEV_ONLY_INVENTORY.md`).
- **Health server binding to 0.0.0.0** is required for kubelet probes.
- **Missing Dockerfile HEALTHCHECK**: Kubernetes ignores it and uses the chart's probes.
  The same image runs api/worker/scheduler with different health endpoints.
- **`site/assets/js` innerHTML/RegExp findings**: the search query is HTML-escaped and
  regex-escaped. Everything else comes from the build-generated `search.json`.
- **Go stdlib 1.25.5** was already fixed by the 1.27.2 bump in `359339f1`, after the
  scanned snapshot.
- **containerd, docker, moby, and sigstore** appear only in the vendored iceberg-go
  `go.mod` (its integration-test dependencies) and are not in the engine build graph.

## Still open

- `google.golang.org/grpc` 1.80: the advisory's fix is only in `1.85.0-dev`. Bump when a
  stable release ships.
- Rust lockfiles (`cryo-engine/Cargo.lock`, `cryo-native/Cargo.lock`): run `cargo update`
  for pyo3, quick-xml, rustls, h2, crossbeam-epoch, and rkyv (cargo wasn't available
  where this was done).
- `readOnlyRootFilesystem` (see above).
