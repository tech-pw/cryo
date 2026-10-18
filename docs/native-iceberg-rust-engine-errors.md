# Native Iceberg Rust Engine Review

Reviewed areas:

- `cryo-engine/` Rust subprocess implementation
- `cryo/engines/native/` Python payload, runner, and engine adapter
- Native local Docker execution path

> **Re-validated 2026-07-07.** All 17 findings below still reproduce against the current
> working tree. This pass adds three things:
> 1. [Re-validation status](#re-validation-status-2026-07-07) — what each original finding means today.
> 2. [Additional gaps found in re-review](#additional-gaps-found-in-re-review) — issues #18–#27 not in the original list.
> 3. [Phased remediation plan](#phased-remediation-plan) — an ordered, upstream-aware plan to make the
>    iceberg-rust + DataFusion maintenance engine actually work end-to-end.
>
> **Key clarification about "it worked" in the successful test session:** the earlier local test
> only proved that, after adding `RestCatalogBuilder::with_storage_factory(OpenDalStorageFactory::S3{..})`,
> `cryo-engine` can *connect to the REST catalog and load a table from MinIO*. All four ops returned
> `ok: true` **with zero changes** — they were still analysis stubs. The only op that performs a real
> write today is `expire_snapshots` (metadata only). "Loads the table" is not "does maintenance".

## Errors And Risks

### 1. Execute-mode native maintenance is advertised but mostly unimplemented

Severity: High

`native-datafusion` is seeded with `operation_mode: "execute"`, and the default native preset includes `expire_snapshots`, `remove_orphan_files`, `optimize`, and `optimize_manifests`. In Rust, only `expire_snapshots` attempts a real Iceberg transaction. The other three execute-mode operations return failed operation results with "not yet implemented" messages:

- `cryo-engine/src/operations.rs`: `rewrite_data_files`
- `cryo-engine/src/operations.rs`: `remove_orphan_files`
- `cryo-engine/src/operations.rs`: `rewrite_manifests`
- `scripts/seed_local_config.py`: `LOCAL_DEFAULT_NATIVE_PRESET`

Impact: default native deep runs will fail after snapshot expiry, and native optimize/orphan/manifests cannot currently work as a replacement for Trino/Spark.

Suggested fix: either gate the native default preset to supported operations only, or keep the destination in dry-run/analyze mode until the Rust write paths exist. The UI/API should label unsupported operations before queueing the run.

### 2. Partition predicates are serialized by Python but ignored by Rust

Severity: High

Python sends partition predicates as a top-level `predicates` field in each operation payload:

- `cryo/engines/native/payload.py`: `_predicate_payload`

Rust deserializes only `type` and `params` in `OperationRequest`; unknown fields are ignored:

- `cryo-engine/src/main.rs`: `OperationRequest`

Impact: partition-aware optimize chunks planned by Cryo are lost at the Rust boundary. Once `rewrite_data_files` execution is implemented, this could accidentally rewrite a much larger table scope than planned.

Suggested fix: add predicate fields to the Rust request schema and make `rewrite_data_files` enforce them. Until then, native optimize should reject operations with predicates rather than ignoring them.

### 3. Remove-orphan parameter names do not match

Severity: Medium

The planner emits orphan parameters as `retention_days`:

- `cryo/planner/planner.py`: `_orphan_params`

Rust reads `min_age_hours` or `orphan_min_age_hours`:

- `cryo-engine/src/operations.rs`: `remove_orphan_files`

Impact: configured orphan retention is ignored and Rust falls back to the hard-coded 72-hour default. This can make dry-run counts wrong and would make future execute mode use the wrong safety window.

Suggested fix: normalize on one contract. Prefer `orphan_min_age_hours` in the planner/preset API, or teach Rust to convert `retention_days` to hours.

### 4. Docker image depends on a pre-existing `cryo-engine` binary

Severity: High

The Docker image exposes `/app/cryo-engine/target/release` on `PATH` and preserves an existing `cryo-engine/target/release/cryo-engine` binary, but it does not build `cryo-engine` during image creation:

- `Dockerfile`: `PATH` and `chmod` for `cryo-engine/target/release/cryo-engine`
- `.dockerignore`: includes only the existing release binary under `cryo-engine/target`
- `.gitignore`: ignores `target/`

Impact: a fresh checkout or CI image build without a prebuilt `target/release/cryo-engine` artifact will produce a worker image without the native binary. Native worker startup can still succeed, but native job execution will fail when the engine factory cannot find `cryo-engine`.

Suggested fix: add a multi-stage Docker build that compiles `cryo-engine` for the image platform, or download/copy a CI-built artifact explicitly. Add a worker startup check or health check that verifies `cryo-engine execute` is spawnable.

### 5. S3 path-style and region settings are not forwarded to Rust

Severity: High

Local and on-prem S3-compatible catalogs often require path-style addressing and explicit region. Source configs include keys such as `s3.path-style-access` and `s3.region`, but the native payload only forwards endpoint and access keys:

- `scripts/seed_local_config.py`: local source includes `s3.path-style-access`
- `cryo/engines/native/payload.py`: `source_catalog_config`
- `cryo-engine/src/operations.rs`: `load_catalog`

Impact: REST catalog/table load or commit can fail against MinIO and S3-compatible endpoints even when the same source works via PyIceberg/Trino/Spark.

Suggested fix: map `s3.path-style-access`, `s3.region`, and any other required Iceberg/OpenDAL S3 properties through the Python payload and into Rust catalog/storage properties. Add a local MinIO smoke test.

### 6. Top-level Rust response can report `ok: true` even when an operation failed

Severity: Medium

The Rust loop only sets `all_ok = false` when `execute_operation` returns `Err`. Several operation functions return `Ok(OperationResult { success: false, ... })`, so `ExecuteResponse.ok` can remain true even though a result failed:

- `cryo-engine/src/main.rs`: `execute`
- `cryo-engine/src/operations.rs`: unimplemented execute-mode operations

Python currently catches the first failed operation in `_exec_result_from_native`, but the subprocess exit code and top-level response are misleading for any other consumer or diagnostic tooling.

Suggested fix: compute top-level `ok` from `results.iter().all(|r| r.success)` and exit non-zero when any operation result failed.

### 7. Native run console progress is not wired

Severity: Medium

`cryo-engine` emits progress JSONL to stderr, and the runner can parse it through `on_progress`, but `NativeEngine.execute` does not pass an `on_progress` callback:

- `cryo-engine/src/progress.rs`
- `cryo/engines/native/runner.py`: `execute_request`
- `cryo/engines/native/engine.py`: `execute`

Impact: native runs do not stream progress to the run console despite the README claiming they do.

Suggested fix: pass a callback from the worker execution context through `NativeEngine.execute` into `execute_request`, or remove the progress claim until event plumbing exists.

### 8. Rust stderr mixes progress events and tracing logs

Severity: Medium

Both progress JSON and tracing JSON are written to stderr:

- `cryo-engine/src/main.rs`: tracing subscriber uses stderr
- `cryo-engine/src/progress.rs`: progress events use stderr

The Python runner parses every JSON object from stderr as progress when a callback is supplied:

- `cryo/engines/native/runner.py`: stderr parsing loop

Impact: once progress forwarding is enabled, structured logs may be forwarded as progress events unless the Python side filters for the progress schema.

Suggested fix: filter parsed stderr JSON for `event in {"started", "progress", "completed", "failed"}` and an `operation` field, or separate logs from progress by stream/prefix.

### 9. Native capacity accounting does not reflect running subprocesses

Severity: Medium

Native capacity is currently static and based on CPU/slot cap. It does not probe or count running `cryo-engine` subprocesses:

- `cryo/engines/native/engine.py`: `probe_capacity`
- `cryo/governor/capacity.py`: native destinations use static capacity

Impact: the governor may admit more native jobs than intended if Redis/DB in-flight accounting and live subprocess state diverge. This is especially risky because each native job runs a local subprocess in the worker container.

Suggested fix: expose native in-flight counts from the queue/DB to capacity probing, or make the governor's local in-flight accounting the authoritative native capacity source.

### 10. Multi-level Iceberg namespaces are unsupported

Severity: Low

`native_table_ref` strips exactly one catalog prefix from three-part names, and Rust accepts only `schema.table` or `catalog.schema.table`:

- `cryo/engines/native/payload.py`: `native_table_ref`
- `cryo-engine/src/operations.rs`: `parse_table_ident`

Impact: tables in multi-level namespaces, such as `catalog.org.schema.table`, cannot be addressed by the native engine.

Suggested fix: treat the last component as table name and all preceding non-catalog components as namespace parts, or pass catalog and namespace/table separately in the request.

### 11. Local Docker native runs require container-reachable source endpoints

Severity: Medium

The native worker now runs in Linux containers, so source configs used by native jobs must point to container-reachable hosts such as `iceberg-rest` and `minio`, not host-local `localhost` URLs:

- `scripts/seed_local_config.py`: local REST/HMS source defaults
- `cryo/engines/native/payload.py`: source catalog config forwarding

Impact: local native runs against a source seeded with host-local endpoints can fail from inside `cryo-worker`, even though the same source may work from host-run scanner/API code.

Suggested fix: seed Docker-mode source configs with Compose service names, or have `start-local.sh` select container-safe local source endpoints when `START_CRYO_DOCKER=1`.

### 12. Two Rust native stacks are conflated

Severity: Medium

Worker maintenance uses the `cryo-engine` subprocess, while `NativeEngine.compact_files`, `native_binpack`, `native_sort`, and `native_zorder` use the optional `cryo_native` PyO3 module. These are separate Rust code paths:

- `cryo-engine/`
- `cryo-native/`
- `cryo/engines/native/engine.py`

Impact: tests and docs can appear to validate “native engine” behavior while exercising the PyO3 path instead of the worker subprocess path.

Suggested fix: split naming/docs/tests clearly between `cryo-engine` subprocess execution and optional `cryo_native` PyO3 helpers. Avoid using PyO3 tests as evidence that worker native maintenance works.

### 13. Native result file counters are not meaningful for optimize

Severity: Low

The Python adapter maps `files_rewritten` to both `input_files_count` and `output_files_count`:

- `cryo/engines/native/engine.py`: `_exec_result_from_native`

Impact: compaction effectiveness metrics will be wrong once native rewrite execution exists, because input and output file counts are distinct values.

Suggested fix: extend the Rust response with separate input/output file counts and map them separately in the adapter.

### 14. Operation-specific audit fields are dropped

Severity: Low

`_exec_result_from_native` only maps `files_rewritten` and `bytes_removed` into `ExecResult`. Fields such as `snapshots_expired` and `manifests_rewritten` are not represented in audit details:

- `cryo-engine/src/main.rs`: `OperationResult`
- `cryo/engines/native/runner.py`: `NativeOperationResult`
- `cryo/engines/native/engine.py`: `_exec_result_from_native`

Impact: successful native expire/manifests operations will not produce useful operation-specific audit metrics.

Suggested fix: extend `ExecResult` or audit detail payloads to preserve operation-specific native result fields.

### 15. Documentation overstates native engine readiness

Severity: Low

Several docs describe the native engine as complete or first-class while the execute paths are partial:

- `docs/EXECUTION_PLAN.md`
- `docs/current-state.md`
- `docs/ARCHITECTURE_AND_AI_STRATEGY.md`
- `cryo-engine/README.md`

Impact: operators may select the native default preset expecting full maintenance behavior, but most operations fail or only analyze.

Suggested fix: document the exact supported execute operations and mark native optimize/orphan/manifests as experimental until implemented.

### 16. Rust dependency pin is not reproducible

Severity: Low

`cryo-engine` depends on `iceberg-rust` Git `main`:

- `cryo-engine/Cargo.toml`

Impact: builds can change or break without repository changes.

Suggested fix: pin to a tag or commit SHA, or use a crates.io release when available.

### 17. Missing end-to-end subprocess tests

Severity: Low

Existing Python runner tests use a fake script; Rust tests are isolated from the Python adapter. There is no test that runs Python payload generation through a real `cryo-engine` binary against a local catalog:

- `tests/test_native_runner.py`
- `tests/test_native_engine.py`
- `.github/workflows/ci.yml`

Impact: protocol, S3 config, predicate passthrough, and execute-mode failures can regress without CI catching them.

Suggested fix: add an integration test that builds/runs `cryo-engine` through the Python runner against a local REST catalog/MinIO fixture, plus golden tests for native payloads.

---

## Re-validation status (2026-07-07)

Every original finding was re-checked against the current tree. Nothing has been fixed; a few
impacts are now more severe than originally graded.

| # | Finding | Status | Notes after re-check |
|---|---------|--------|----------------------|
| 1 | Execute-mode mostly unimplemented | **Confirmed** | `rewrite_data_files`, `remove_orphan_files`, `rewrite_manifests` still return `success:false`, "not yet implemented" in execute mode (`cryo-engine/src/operations.rs`). |
| 2 | Predicates serialized but ignored by Rust | **Confirmed + worse** | `OperationRequest` (`main.rs`) still only reads `type`/`params`. See new **#18** — predicates are also dropped inside Python before they reach the wire. |
| 3 | Orphan param name mismatch | **Confirmed** | Planner `_orphan_params` emits `retention_days` (days); Rust reads `min_age_hours`/`orphan_min_age_hours`. Seeded preset params have neither. Rust falls back to 72h. |
| 4 | Docker image needs a pre-built binary | **Confirmed** | `Dockerfile` still only `chmod`s an existing binary; `.dockerignore` preserves `!cryo-engine/target/release/cryo-engine`. No build stage. |
| 5 | S3 path-style/region not forwarded | **Confirmed** | `payload.source_catalog_config` forwards only `s3_endpoint`/keys. `s3.path-style-access`/`s3.region` are dropped. See **#22**. |
| 6 | Top-level `ok` can be true after a failed op | **Confirmed** | `main.rs::execute` only flips `all_ok` on `Err`; unimplemented ops return `Ok{success:false}` → `ok:true`, exit 0. |
| 7 | Progress not wired to run console | **Confirmed** | `NativeEngine.execute` never passes `on_progress`; `ExecCtx` has no callback channel. |
| 8 | stderr mixes progress + tracing JSON | **Confirmed** | `main.rs` tracing writer and `progress.rs` both write JSON to stderr; runner treats every stderr JSON line as a progress event. |
| 9 | Native capacity is static | **Confirmed** | `probe_capacity` returns `min(cpu, slot_cap)`, ignores live subprocesses. |
| 10 | Multi-level namespaces unsupported | **Confirmed** | `parse_table_ident` + `native_table_ref` handle only 2/3-part names. |
| 11 | Docker native needs container-reachable endpoints | **Confirmed + high impact now** | `seed_local_config.py` `LOCAL_SOURCE` still uses `localhost:8181`/`localhost:9000`. The new Dockerized worker (from the Docker-hosting session) cannot reach `localhost`, so every native run against the seeded source fails from inside `cryo-worker`. |
| 12 | Two Rust stacks conflated | **Confirmed** | `NativeEngine` still mixes `cryo-engine` subprocess (worker path) and `cryo_native` PyO3 (compaction helpers). |
| 13 | File counters not meaningful | **Confirmed** | `_exec_result_from_native` maps `files_rewritten` to both `input_files_count` and `output_files_count`. |
| 14 | Operation-specific audit fields dropped | **Confirmed** | `snapshots_expired`/`manifests_rewritten` are parsed into `NativeOperationResult` but never mapped into `ExecResult`/audit. |
| 15 | Docs overstate readiness | **Confirmed** | `docs/current-state.md`, `lld.md` P9, README still read as "done". |
| 16 | Rust dependency pin not reproducible | **Confirmed + worse** | Still `iceberg-rust` git `main`; the write actions we depend on are actively being refactored upstream (see **#26**), so `main` can break the build without any repo change. |
| 17 | Missing end-to-end subprocess tests | **Confirmed** | `tests/test_native_runner.py` uses a fake Python script; the CI `cryo-engine` job only runs `cargo fmt/clippy/test/build`. No payload→binary→catalog test. |

## Additional gaps found in re-review

These are not in the original 1–17 list.

### 18. Partition predicates are dropped inside Python before the payload is built

Severity: High

Finding #2 says Rust ignores predicates. It is worse than that: the worker path loses predicates
**before** they reach the Rust boundary.

- `NativeEngine.render` stores `_operation_to_dict(operation)` in `RenderedStatement.payload`, and
  `_operation_to_dict` serializes only `{"type", "params"}` — `operation.predicates` is discarded.
- `NativeEngine.execute` rebuilds the `Operation` via `_operation_from_payload`, which also reads only
  `type`/`params`, so the reconstructed operation has empty `predicates`.
- Therefore `build_execute_request` → `_predicate_payload(operation)` always returns `{}` for worker
  runs. The predicate-serialization code in `payload.py` is effectively dead for the worker path.

Impact: the Planner's whole reason for existing — partition-scoped, GB-budgeted OPTIMIZE chunks that
protect Trino — is silently flattened to "no predicate" before it can be honored. Once
`rewrite_data_files` execute lands, this would rewrite the entire table instead of the planned chunk.

Suggested fix: carry `predicates` through `_operation_to_dict`/`_operation_from_payload`, then through
`build_execute_request`, and enforce them in Rust (ties into #2). Until compaction execute exists,
reject an OPTIMIZE operation that carries predicates rather than executing a full-table scope.

### 19. The default native deep run fails on the second operation

Severity: High

The seeded `default-native-deep` preset (mode `deep`) plans, in order,
`expire_snapshots → remove_orphan_files → optimize → optimize_manifests`. The worker aborts the whole
job on the first `ExecResult.ok == False` (`cryo/worker/worker.py`, `break` on failed result). Since
`remove_orphan_files` returns a not-implemented failure in execute mode, the job is marked **FAILED**
immediately after expiry commits.

Impact: the default preset can never complete successfully today. Worse, expiry has already mutated
metadata by the time the job fails, so the run is a hard failure that also left files behind (see #20).

Suggested fix: gate the default preset to only the operation(s) that truly execute today
(`expire_snapshots`), or make the native engine report unsupported execute ops as **SKIPPED**
(recorded, visible, non-fatal) instead of aborting the run. See Phase 0.

### 20. `expire_snapshots` commits metadata only and leaks files forever

Severity: High

iceberg-rust's `ExpireSnapshotsAction` intentionally *does not delete data/manifest files* — it only
emits `RemoveSnapshots`/`RemoveSnapshotRef` metadata updates (upstream #2591). Because
`remove_orphan_files` is unimplemented (#1), the data and manifest files belonging to expired snapshots
are never cleaned up.

Impact: every native deep run that expires snapshots permanently orphans those files in object storage.
This is not a harmless no-op — it is a monotonic storage leak that the platform is supposed to prevent.

Suggested fix: never ship `expire_snapshots` in execute mode as the "working" op without a paired,
working `remove_orphan_files`. Document the coupling; implement orphan removal in the same phase (Phase 2).

### 21. `snapshots_expired` is a hand-rolled count, not the action's result

Severity: Medium

`count_expirable_snapshots` reimplements a simplified `age + retain_last` rule and (a) is used to decide
whether to commit at all (`to_expire == 0` short-circuits) and (b) is the reported metric. It does not
match `ExpireSnapshotsAction` semantics (per-branch ancestry, `min-snapshots-to-keep`, ref aging,
`history.expire.*` table-property defaults). The reported count can disagree with what the transaction
actually expires, and the pre-check can skip a commit the action would have performed (or vice versa).

Suggested fix: drive both the decision and the reported count from the action's own plan/result rather
than a parallel counter.

### 22. REST-catalog auth and full storage properties are not forwarded

Severity: Medium (extends #5)

`source_catalog_config` forwards only `uri`, `warehouse`, `s3.endpoint`, and the two S3 keys. It does
not forward:
- `s3.path-style-access`, `s3.region` (OpenDAL S3 typically *requires* a region; MinIO needs
  `region=us-east-1` + path-style).
- REST OAuth2 / token auth (`token`, `credential`, `oauth2-server-uri`, `scope`) or SigV4 — required by
  Polaris/Unity/Tabular-style REST catalogs.

Impact: native table load/commit fails against any source that needs region, path-style, or REST auth,
even when Trino/Spark/PyIceberg work against the same source.

Suggested fix: pass the source config's storage/auth keys through generically (a `properties` map) into
the Rust catalog + storage builders instead of a hard-coded allow-list of three keys.

### 23. One subprocess per operation — N catalog loads, no atomicity, partial-commit risk

Severity: Medium

The worker renders one `RenderedStatement` per operation, and each spawns a fresh `cryo-engine execute`
that reloads the catalog and table. The binary already accepts a multi-op request
(`ExecuteRequest.operations: [...]`), but the adapter never uses it.

Impact: 4× catalog/table loads per deep run; operations cannot be composed into one atomic transaction;
a mid-sequence failure (expire ok, then orphan fails) leaves the table partially maintained with no
rollback and no single audit envelope.

Suggested fix: send the whole plan as one request and execute the ordered ops in a single process
(optionally one shared transaction where upstream supports it). Map per-op results back to per-op audit
rows.

### 24. No usable dry-run / analyze path from the preset

Severity: Medium

The destination `operation_mode` is hard-seeded to `"execute"`, and `build_execute_request` derives the
request mode from `operation_mode` while ignoring `plan.mode`. Analyze mode is the *only* mode where
optimize/orphan/manifests return success today, but there is no preset/plumbing to select it.

Impact: operators cannot safely "preview" a native deep run; combined with #19 the default preset can
only fail.

Suggested fix: expose `analyze`/`dry_run` as a first-class preset/run option and thread `plan.mode` (or
an explicit run flag) into the request mode.

### 25. `compute_statistics` (Puffin) is silently skipped by native

Severity: Low

If a preset enables `compute_statistics`, the Planner emits a `COMPUTE_STATISTICS` operation.
`_NATIVE_OPERATION_TYPES` and `NativeEngine.render` do not include it, so it is dropped with only a
warning log and the run still reports success.

Suggested fix: either implement/route it or surface it as an explicit unsupported/SKIPPED result so a
"successful" run does not silently omit requested work.

### 26. Compaction is blocked on upstream iceberg-rust write actions

Severity: High (planning constraint)

As of mid-2026, the commit primitive needed for `rewrite_data_files` is not production-ready upstream:
`RewriteFilesAction` (PR #1606) is a draft, `ReplaceDataFilesAction` (PR #2106) was closed stale, and
the "missing write actions" EPIC (#2269) still lists `RewriteFiles`/`Delete`/`Overwrite` as missing.
`RewriteManifestsAction` exists but is not yet integrated into the `SnapshotProducer`/commit path
(issue #2556).

Impact: "just implement the stubs" is not achievable for compaction on stock `iceberg-rust` today. The
headline "DataFusion compaction" feature is the hardest and least-supported op — it must be sequenced
last and may require vendoring a draft action behind a flag or keeping OPTIMIZE on Trino/Spark in the
interim.

Suggested fix: sequence per the phased plan; pin to a specific upstream commit; gate compaction behind a
capability flag until the replace-files action is stable.

### 27. `is_default` uniqueness index vs. runtime enforcement can diverge (multi-tenant)

Severity: Low (moot for default seeding as of migration `20260707_0020`)

The migration adds a partial unique index (`uq_presets_one_default_per_tenant`) and the API clears other
`is_default` flags per tenant. The local seed script no longer sets `is_default=True` on
`default-native-deep`, so the seed-vs-API divergence described below no longer applies to fresh local
setups. If an operator later marks a preset as platform default via the API, the API path already
clears siblings first.

Suggested fix (if re-enabling seeded defaults): route all default-preset writes through one helper
that clears siblings first, and cover with a test.

## Phased remediation plan

Goal: an iceberg-rust + DataFusion maintenance engine that (a) never silently no-ops or leaks, (b)
executes the ops it advertises, and (c) can safely be the default workflow. Phases are ordered so each
one leaves the system in a correct, shippable state.

### Phase 0 — Make the default honest and non-failing (no Rust write code; ~unblock now)

Objective: stop shipping a default preset that hard-fails and silently leaks.

1. **Gate the default preset** (`scripts/seed_local_config.py`, migration `..0011`): reduce
   `default-native-deep` operations to what actually executes safely today. Two acceptable options —
   pick one and document it:
   - `operations: ["expire_snapshots"]` **paired with** working orphan removal (defer to Phase 2), or
   - keep deep but route `optimize` (and `optimize_manifests` until Phase 2) to Trino/Spark, with native
     handling only expiry/orphans.
2. **Non-fatal unsupported ops** (`cryo/engines/native/engine.py`, `runner.py`, worker audit): map a
   Rust `success:false, error:"...not yet implemented..."` to a **SKIPPED/UNSUPPORTED** audit outcome
   that does not abort the run, so expiry still commits and the job ends `PARTIAL`/`SUCCEEDED-with-skips`
   rather than `FAILED` (#19).
3. **Fix orphan param contract** (#3): have the planner emit `orphan_min_age_hours`
   (`orphan_retention_days * 24`) *and/or* teach Rust to read `retention_days`. Add a golden test.
4. **Fix predicate drop in Python** (#18): carry `predicates` through `_operation_to_dict` /
   `_operation_from_payload` / `build_execute_request`; for now, if an OPTIMIZE execute carries
   predicates and compaction isn't implemented, fail clearly instead of widening scope.
5. **Forward full storage/auth config** (#5, #22): pass source storage/auth keys generically
   (`s3.region`, `s3.path-style-access`, REST `token`/`credential`) into the request.
6. **Docker-reachable local endpoints** (#11): when `START_CRYO_DOCKER=1`, seed the local source with
   Compose service names (`iceberg-rest:8181`, `minio:9000`) or remap at runtime.

Exit criteria: a default native run against local Docker completes without a hard failure, with expiry
committing and unsupported ops clearly marked skipped; no localhost-from-container failures.

#### Phase 0 progress (2026-07-07)

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Gate default preset to `expire_snapshots` only | **Done** | `scripts/seed_local_config.py`, migration `20260706_0011`, new migration `20260707_0012` for existing DBs |
| 2 | Non-fatal unsupported ops → SKIPPED audit | **Done** | `ExecResult.skipped`, `NativeEngine._exec_result_from_native`, worker continues on skip |
| 3 | Fix orphan param contract | **Done** | Planner emits `orphan_min_age_hours`; Rust reads `retention_days` → hours |
| 4 | Fix predicate drop in Python | **Done** | `operation_to_dict`/`operation_from_dict` in `payload.py`; OPTIMIZE+predicates fails clearly in execute mode |
| 5 | Forward full storage/auth config | **Done** | `catalog_properties` map in payload + Rust `ExecuteRequest` |
| 6 | Docker-reachable local endpoints | **Done** | `START_CRYO_DOCKER=1` seeds `iceberg-rest`/`minio`; `start-local.sh` passes env to seed |

**Tests added:** `test_orphan_params_emit_min_age_hours`, `test_source_catalog_config_forwards_storage_and_auth`,
`test_operation_roundtrip_preserves_predicates`, `test_unsupported_native_op_maps_to_skipped_exec_result`,
`test_worker_continues_when_engine_skips_unsupported_operation`.

**Decision:** default-native-deep is gated to `expire_snapshots` only until Phase 2 pairs it with working
`remove_orphan_files` (see finding #20 — expiry without orphan cleanup leaks files).

### Phase 1 — Protocol & correctness hardening (Rust + Python; still no heavy writes)

Objective: make results trustworthy and observable, and make the toolchain reproducible/testable.

1. **Honest top-level status** (#6): `ok = results.iter().all(|r| r.success)`; process exit code reflects
   it.
2. **Separate progress from logs** (#7, #8): emit progress on stdout (or a dedicated stream/prefix) and
   tracing on stderr; Python filters for the progress schema; thread `on_progress` from
   `ExecCtx`/worker → `NativeEngine.execute` → `execute_request` → run console.
3. **Single multi-op subprocess** (#23): send the ordered plan as one request; per-op results → per-op
   audit rows.
4. **Enforce predicates in Rust** (#2): deserialize `predicates` in `OperationRequest`; reject/execute
   accordingly.
5. **Multi-level namespaces** (#10): treat the last component as table, preceding non-catalog components
   as namespace parts (or pass namespace/table separately).
6. **Map operation-specific results to audit** (#13, #14): distinct input/output file counts;
   surface `snapshots_expired`/`manifests_rewritten`.
7. **Reproducible build + real e2e test + Docker build stage** (#4, #16, #17): pin `iceberg-rust` to a
   commit SHA; add a multi-stage Docker build (or CI artifact) that compiles `cryo-engine` for the image
   arch + a worker startup health check; add an integration test that runs the Python payload through a
   real `cryo-engine` binary against a local REST catalog + MinIO fixture in CI.

Exit criteria: CI builds the binary reproducibly and runs a payload→binary→catalog test; run console
shows native progress; a failed op reports non-zero exit and `ok:false`.

#### Phase 1 progress (2026-07-07)

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Honest top-level status (#6) | **Done** | `main.rs`: `ok = all_ok && results.iter().all(\|r\| r.success)`; exit code follows |
| 2 | Separate progress from logs (#7, #8) | **Done** | Progress on stdout with `CRYO_PROGRESS:` prefix; tracing plain text on stderr; runner filters schema; `ExecCtx.on_progress` wired through worker |
| 3 | Single multi-op subprocess (#23) | **Done** | `build_execute_request_for_operations`, `NativeEngine.execute_plan`, worker `_run_native_plan` |
| 4 | Enforce predicates in Rust (#2) | **Done** | `OperationRequest.predicates`; execute-mode OPTIMIZE with predicates rejected in Rust |
| 5 | Multi-level namespaces (#10) | **Done** | `parse_table_ident` + `native_table_ref` handle `catalog.ns1.ns2.table` |
| 6 | Map operation-specific results (#13, #14) | **Done** | `input_files`/`output_files` in Rust; `ExecResult` + `AuditRecord` carry snapshots/manifests counts |
| 7 | Reproducible build + e2e + Docker (#4, #16, #17) | **Done** | `iceberg-rust` pinned to `6144e10`; multi-stage Dockerfile; `verify_cryo_engine_spawnable`; protocol tests in `test_native_runner.py` |

**Tests added:** `test_native_table_ref_multi_level_namespace`, `test_build_execute_request_for_multiple_operations`, `test_split_stdout_lines`, `test_is_progress_event_filters_tracing`, `test_execute_request_honest_failure_when_operation_not_successful`, `test_operation_result_maps_input_output_files`, `test_exec_result_maps_snapshots_expired`.

**Remaining for full e2e:** addressed in Phase 2 — see `tests/integration/native/` and CI job `native-e2e`.

### Phase 2 — Implement the tractable write ops for real

Objective: native expiry + orphan removal + (best-effort) manifest rewrite that actually change the
table safely. These are achievable on current iceberg-rust.

1. **`expire_snapshots`** (#21): drive the decision and reported count from `ExpireSnapshotsAction`'s own
   plan/result instead of the hand-rolled counter; honor `retention_days`/`retain_last` and (optionally)
   `history.expire.*` defaults.
2. **`remove_orphan_files`** (#1, #20): implement real cleanup — enumerate files under
   `metadata.location()` via the table's FileIO/OpenDAL, diff against files referenced by live
   snapshots/manifests, delete those older than `min_age_hours`. Guard with the safety window and
   `force`. This closes the expiry file-leak (#20) and is the natural pairing with Phase 2.1.
3. **`rewrite_manifests`** (#1): use `RewriteManifestsAction` if its commit path is usable on the pinned
   version; otherwise keep it analyze-only and mark experimental (blocked by upstream issue #2556). Do
   not advertise it as executing until it commits.

Exit criteria: a native `expire_snapshots + remove_orphan_files` deep run on a fragmented local table
expires snapshots **and** frees the orphaned files, with correct audited counts/bytes; verified against
`$files`/object-store listing before and after.

#### Phase 2 progress (2026-07-07)

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | `expire_snapshots` uses action plan/result (#21) | **Done** | `PreviewCatalog` previews analyze counts from `RemoveSnapshots` updates; execute counts via before/after snapshot-id diff; optional `retention_days`/`retain_last` only when set (else `history.expire.*` defaults) |
| 2 | `remove_orphan_files` real cleanup (#1, #20) | **Done** | `orphans.rs`: referenced-path walk via manifest lists + `object_store` listing under table location; execute deletes via table `FileIO` |
| 3 | `rewrite_manifests` gated (#1, upstream #2556) | **Done** | Analyze reports manifest count; execute returns explicit upstream-blocked error (not advertised as executing) |
| 4 | Default preset pairs expire + orphan | **Done** | `scripts/seed_local_config.py`, migration `20260707_0013` |
| 5 | REST+MinIO e2e (expire + orphan bytes) | **Done** | `tests/integration/native/`, `make test-native-e2e-local`, CI `native-e2e` job |

**Tests added/updated:** `orphans.rs` unit tests (`normalize_s3_scheme_aliases`, `parse_table_location`); `test_unsupported_native_op_maps_to_skipped_exec_result` now covers `rewrite_manifests` upstream block.

**E2E (Phase 2 exit criteria):** `tests/integration/native/test_native_expire_orphan_e2e.py` — seeds a REST table with 3 snapshots + deliberate orphan object, runs native `expire_snapshots` + `remove_orphan_files`, asserts orphan bytes removed from MinIO. Local: `make test-native-e2e-local` (requires `cargo build --release` in `cryo-engine/`). CI: `native-e2e` job in `.github/workflows/ci.yml`. REST rejects removing more than one snapshot in a single commit (`commit state is unknown`); Rust execute mode steps `retain_last` down one snapshot at a time, matching `engine-go/internal/ops/expire.go`.

**Phase 2 status:** **Complete** (pending green `native-e2e` CI on merge).

### Phase 3 — DataFusion compaction (`rewrite_data_files`) — headline, hardest, upstream-gated

Objective: real partition-aware compaction with an atomic commit.

- **Dependency**: a stable replace/rewrite-files action in iceberg-rust (PRs #1606/#2106, EPIC #2269).
  Until then, either vendor the draft action behind a capability flag (with heavy tests) or keep OPTIMIZE
  routed to Trino/Spark while native owns expiry/orphans/manifests.
- **Design** (must honor the Planner output — parity checklist §5): per planned chunk (predicate scope),
  read matching data files via Arrow/DataFusion, apply binpack/sort/z-order, write new Parquet via
  `ParquetWriter`, and commit via the replace-files action. Enforce predicates (#2/#18), GB-budget +
  max-partitions-per-chunk caps, oversize-alone, small-table bypass — all already produced by the
  Planner; native must not re-plan or widen scope.
- **Metrics**: report distinct input vs output file counts and bytes read/written (#13).

Exit criteria: a native OPTIMIZE of a partitioned local table compacts only the planned chunk, commits
atomically, and reduces small-file count with accurate before/after metrics; failure commits nothing.

#### Phase 3 progress (2026-07-07)

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Partition predicate evaluation (`predicates.rs`) | **Done** | OR-of-AND matching against manifest `DataFile` partition literals; bucket/truncate rejected in execute |
| 2 | Compaction candidate scan (`compaction.rs`) | **Done** | Predicate-scoped small-file discovery from manifest lists; no live `$files` scan |
| 3 | Real analyze-mode metrics | **Done** | `rewrite_data_files` analyze returns candidate input files/bytes + estimated output file count |
| 4 | Planner param normalization | **Done** | `file_size_mb` → `target_file_size_bytes` in `payload.py`; `partition_field` forwarded in predicates |
| 5 | Protocol: `bytes_written` field | **Done** | `OperationResult.bytes_written` + Python runner parsing |
| 6 | DataFusion read/write pipeline (no commit) | **Done** | `compaction_write.rs`: binpack via FileIO + arrow/parquet; staged under `data/.cryo-compaction-staging/` |
| 7 | Atomic commit via `RewriteFilesAction` | **Blocked** | Pinned `iceberg-rust@6144e10` has no replace-files action (EPIC #2269) |
| 8 | Capability flag for compaction execute | **Done** | `CRYO_NATIVE_COMPACTION_EXECUTE` env + `destination.config.compaction_execute` → request `params` |
| 9 | Compaction e2e (analyze + execute) | **Done** | `tests/integration/native/test_native_compaction_e2e.py` |

**Tests added:** `predicates.rs` unit tests; `compaction.rs` `estimate_output_files`; `test_normalize_optimize_params_maps_file_size_mb`; `test_build_execute_request_forwards_compaction_execute_flag`; `test_native_compaction_*_e2e`.

**Decision:** execute-mode OPTIMIZE remains **SKIPPED** (non-fatal) until upstream `RewriteFilesAction` is
pinned. With `CRYO_NATIVE_COMPACTION_EXECUTE=1` or `compaction_execute: true`, cryo-engine performs a
real binpack write to a staging prefix and reports `bytes_written` / output file counts, but still does
not commit a table snapshot.

**Routing disconnect (2026-07-07):** Worker→native routing now advertises and executes only
`expire_snapshots` + `remove_orphan_files` (`NATIVE_SUPPORTED_OPERATIONS` in
`cryo/engines/native/engine.py`). `OPTIMIZE` (`rewrite_data_files`) and `OPTIMIZE_MANIFESTS`
(`rewrite_manifests`) are dropped non-fatally at the worker with a SKIPPED audit row; the Rust
implementation (`compaction.rs`, `compaction_write.rs`, `predicates.rs`, `operations.rs`) and Python
payload helpers remain intact for direct cryo-engine calls and e2e tests. Re-enable routing once
upstream `RewriteFilesAction` is pinned (task #7).

**Phase 3 status:** **In progress** — staged compaction write + analyze e2e complete; atomic commit (#7)
still blocked on upstream; worker routing disconnected until commit lands.

### Phase 4 — Ops integration, capacity, and docs

Objective: make native a first-class, correctly-governed default.

1. **Live native capacity** (#9): derive capacity from queue/DB in-flight subprocess accounting, not a
   static CPU/slot cap, so the governor cannot over-admit local subprocesses.
2. **Docs truth-in-advertising** (#15): document exactly which ops execute vs analyze per version; remove
   "complete/first-class" claims until Phases 2–3 land.
3. **Split naming/tests** (#12): clearly separate `cryo-engine` (subprocess worker path) from
   `cryo_native` (PyO3 compaction helpers); stop citing PyO3 tests as evidence of worker native
   maintenance.
4. **Default-preset write path** (#27): if platform defaults are reintroduced, route all `is_default`
   writes (seed + API) through one clear-siblings-first helper; add a test. Currently no preset is
   seeded with `is_default=true` (migration `20260707_0020`).

Exit criteria: native is the default deep workflow, governed by live capacity, with docs/tests that match
the implemented behavior for the pinned `iceberg-rust` version.

