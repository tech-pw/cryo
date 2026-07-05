# Cryo Premortem — written 2026-07-05, imagining failure as of 2027-01-05

**Method:** a premortem — assume it's six months from now and something has gone visibly wrong
with Cryo, then work backward to the specific, verifiable-today root causes. Every item below is
grounded in the actual codebase (file/line cited), not speculation. Ordered by blast radius, not
by how easy the fix is — see `docs/PREMORTEM_ACTIONABLES.md` for the prioritized, actionable
version of this list.

The one-sentence version: **Cryo has spent the last few days adding enormous feature breadth
(RBAC, multi-tenancy, ABAC, lineage, AI copilot, MCP, learned cost models, AWS integration) at a
pace that has outrun the foundational hardening those features assume already exists.** The
individual features are well-built. The gap is between what the schema/route decorators *imply*
is enforced and what's actually enforced underneath.

---

## Scenario A: "We got popped." (auth bypass)

**What happens:** Cryo is pitched as a self-hosted governance tool that can run destructive
operations against production Iceberg tables (`expire_snapshots`, orphan-file deletion, manifest
rewrites). Six months in, someone runs a routine pentest — or worse, an internal user pokes at the
API — and discovers that the RBAC decorators on every route
(`dependencies=[Depends(require_viewer/operator/admin)]` across `routes.py`/`advanced_routes.py`)
are enforcing nothing real.

**Root cause, verified today:** `principal_from_token()` in `cryo/api/security.py:63-81` never
verifies a JWT signature — `_claims_from_jwt_payload` (lines 134-147) just base64-decodes the
payload, and bare literal tokens like `"admin"` are accepted outright. `docker-compose.yml` sets
`CRYO_JWT_SECRET: dev-secret-change-in-prod`, but that variable is **read nowhere in the codebase**
— it's decorative. Anyone who can reach the API today can self-mint an admin token. This isn't a
hardening gap to close later; it's a live bypass in a tool whose entire value proposition is
governed, audited, role-gated operations.

## Scenario B: "The AWS keys leaked from a Postgres backup." (secrets at rest)

**What happens:** A routine DB backup or a misconfigured backup-bucket ACL exposes the Cryo
metadata store. Because the AWS integration (built this week, at explicit user request for "free
S3 cost intelligence") stores real IAM access keys, the blast radius isn't "our internal metadata
leaked" — it's "someone else's AWS account is now compromised."

**Root cause, verified today:** `AWSIntegration.secret_ref` (`cryo/db/models.py:58,87,444`) is a
plain `Text` column; `create_aws_integration`/`patch_aws_integration`
(`cryo/api/routes.py:325,350`) write the raw secret access key straight into it, and it's read back
out in plaintext at `routes.py:390,418`. The field name implies indirection to a secret store
(Vault/KMS/Secrets Manager) — there is none. The database *is* the vault today.

## Scenario C: "Tenant B saw Tenant A's tables." (multi-tenancy is schema-only)

**What happens:** Cryo is deployed for more than one team/customer sharing one instance (the
`20260705_0004_multi_tenant.py` migration exists specifically because someone anticipated this).
Someone assumes `tenant_id` means isolation. It doesn't yet.

**Root cause, verified today:** the migration adds `tenant_id` FKs to
`sources/destinations/tables/presets`, but `Principal` (`cryo/api/security.py`,
`cryo/api/deps.py`) carries no tenant concept, and no route filters query results by it. The schema
was built for multi-tenancy; the enforcement layer wasn't. This is worse than having no
multi-tenancy at all, because the column's *presence* invites people to trust an isolation boundary
that isn't wired up.

## Scenario D: "The governor fell over at our real fleet size." (scale ceiling)

**What happens:** A design partner with a genuinely large fleet (the docs' own stated target of
"thousands of tables") turns Cryo on, and wave latency, DB load, or memory climbs in a way nobody
saw in dev/testing (dev fleets are small).

**Root cause, verified today:** `Governor.run_wave()` (`cryo/governor/governor.py:88-260`) opens up
to three separate DB sessions **per pending job, per wave** (trigger evaluator, approval check, PII
gate — lines 136, 163, 189) with no batching — O(n) round-trips per wave, not O(1). Separately,
`cryo/ai/similarity.py::find_similar_tables()` recomputes an embedding for *every* fleet row on
*every* call with no caching (confirmed called live from `cryo/api/extensions.py:361`) — O(n) per
lookup, effectively O(n²) if surfaced fleet-wide, exactly the scale this feature was built to serve
("thousands of tables, no naming convention" — the doc's own framing). And the governor's learned
bandit state lives in a local file, `/tmp/cryo/admission_bandit.json`
(`cryo/governor/governor.py:348`) — it silently resets on container restart and can't be shared
across governor replicas, so scaling the governor horizontally quietly discards its own learning.

## Scenario E: "We shipped a Helm chart that doesn't deploy anything." (deployment story is a stub)

**What happens:** A user takes the "self-hosted" pitch at face value, tries to actually run Cryo on
Kubernetes via the advertised chart, and gets nothing.

**Root cause, verified today:** `deploy/helm/cryo/` contains only `Chart.yaml`, `values.yaml`, and a
34-line `_helpers.tpl` — **no Deployment, Service, or CronJob templates exist.** `docker-compose.yml`
is a real, working multi-process topology (api/scanner/scheduler/governor/worker as separate
containers) — but it's the *only* deployment path that actually works today, and it's not what a
production k8s user reaches for.

## Scenario F: "The Rust engine we advertised never actually ran." (vaporware differentiator)

**What happens:** The native Rust execution engine — a named architectural decision
(`docs/ARCHITECTURE_AND_AI_STRATEGY.md` §1.5) — gets asked about by a technical evaluator, who finds
it's never been built or exercised by CI, let alone in production.

**Root cause, verified today:** two disconnected Rust efforts exist — `cryo-engine/` (standalone
binary, pinning Apache `iceberg`/`iceberg-catalog-rest` to an unpinned `git ... branch = "main"`)
and `cryo-native/` (PyO3 extension, pinning pre-1.0 `iceberg = "0.4"`). Neither builds in CI
(`.github/workflows/ci.yml` has no Rust step at all). `cryo/engines/native/engine.py:33-45` falls
back gracefully when the native module import fails, and every real native-engine test in
`tests/test_native_engine.py` is `@pytest.mark.skipif(not is_native_available())` — only the mocked
test class actually runs. Six months of feature work could pass by without anyone noticing the
flagship performance engine has never executed once in CI.

## Scenario G: "We were flying blind during the incident." (no tracing, silent failure paths)

**What happens:** Something goes wrong in production — a maintenance job that shouldn't have run,
did; or one that should have, didn't — and the on-call person opens the logs to reconstruct what
happened, and can't.

**Root cause, verified today:** there's real Prometheus metrics (`cryo/observability/metrics.py`)
and structured logging (`cryo/observability/logging.py`), but **no distributed tracing and no
error-tracking integration** (no OTel, no Sentry) anywhere in the codebase. Worse, several exception
paths inside `governor.py` (lines 152-153, 179-180, 208-209) are bare
`except Exception: ... continue`/`pass` around trigger evaluation, approval checks, and the PII
gate — exactly the code paths that decide whether a destructive operation runs. A bug there doesn't
crash loudly; it silently skips a safety check or silently never fires a needed one, and the only
trace is whatever got logged, if anything did.

## Scenario H: "A routine `pip install` broke the entire scanning pipeline." (dependency risk)

**What happens:** An unrelated dependency update (CI cache miss, new environment, Dependabot PR)
pulls a new `pyiceberg` release, and something in manifest reading — the same layer this session's
zero-scan quality work depends on — silently changes behavior.

**Root cause, verified today:** `pyproject.toml` pins `pyiceberg[s3fs,pyarrow]>=0.11.0` — open-ended,
pre-1.0, and it's the single dependency the entire scanner (`analyze_table`, `ColumnQualityAccumulator`,
cost accounting) sits on top of. `boto3` is similarly unpinned. Pre-1.0 packages make no
backward-compatibility promise; an open floor with no ceiling is a standing invitation for a routine
rebuild to become an incident.

## Scenario I: "The migrations are becoming a minefield." (schema churn without consolidation)

**What happens:** A future migration silently conflicts with, duplicates, or contradicts an earlier
one — the same class of bug already found and fixed *this session* (six real bugs in one migration
file: duplicate index creation, wrong table names, wrong column names, a type mismatch on a
duplicated column) — except next time nobody happens to be doing a full audit when it happens.

**Root cause, verified today:** 16 migration files exist, most dated within a single 2-3 day window
(multi-tenancy, ABAC, lineage, AWS integration, performance indexes all landed in rapid succession),
with the AWS integration migration already having had to be written as a *merge* migration to
resolve two heads that had diverged. Velocity is outrunning migration review.

## Scenario J: "Nobody noticed the docs were wrong until someone finally checked." (process risk)

**What happens:** This is the meta-risk, and it's not hypothetical — it happened repeatedly *within
this very session*: `cryo/quality/profiler.py` was found reading from a dict key
(`raw["manifest_stats"]`) that the scanner never wrote, silently dead in production, passing its own
tests only because the tests hand-seeded the phantom key. Duplicate AWS integration frontend code
was independently built twice by two concurrent sessions before being reconciled. An undefined
function (`_puffin_ndv_counts`) was briefly callable in committed code after a concurrent edit added
a call site without a definition.

**Root cause:** very high development velocity, apparently including concurrent/parallel AI-driven
sessions editing the same files, without a process step that verifies documentation and tests
against actual runtime behavior before trusting them. Nothing here is a single bug — it's evidence
that the codebase's *self-description* (docs, tests, even variable/field names implying
indirection that doesn't exist, like `secret_ref`) has drifted from reality more than once already,
and nothing structural prevents it from happening again at the same rate features are added.

---

## What's *not* on this list, deliberately

CI is in genuinely good shape (`ci.yml` runs gitleaks, ruff, mypy, and the full pytest suite against
real Postgres+Redis containers, not just mocks) — 275+ tests passing against a real database is a
real asset, not a false confidence signal, for the layers it covers. The AI/ML feature layer
(bandit tuning, learned cost model, embeddings, TabPFN classifier) is honestly scoped, has real
fallback paths, and was independently verified this session to be tested and production-wired —
that work is not what breaks in six months. The risk is concentrated in the *foundational* layers
(auth, secrets, tenancy enforcement, deployment, scale ceilings, observability) that the newer
feature layers implicitly assume are solid.
