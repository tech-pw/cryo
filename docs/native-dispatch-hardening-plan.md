# Native dispatch hardening plan

The create-on-admit native Kubernetes Job path is usable after the immediate
correctness fixes, but its durable lifecycle should not depend on pod logs or
cross-system calls completing in one Governor tick. Implement the following
before treating multi-table dispatch as fully production-grade.

## 1. Make dispatch creation transactional and idempotent

- Add an explicit state machine: `planned -> creating -> running -> finalizing ->
  terminal`, plus `cancel_requested -> cancelling -> cancelled`.
- Assign a deterministic dispatch UUID and Kubernetes Job name before any
  external call.
- Add a unique constraint for the active dispatch of a maintenance job and a
  unique `(namespace, k8s_job_name)` constraint.
- Write a dispatch outbox row in the same transaction that attaches member
  jobs. A reconciler, not the admission request, creates the Kubernetes Job.
- Treat Kubernetes `AlreadyExists` as reconciliation: read and adopt the
  deterministic Job instead of creating another one.
- Add crash-point tests after each DB/Kubernetes transition and prove that no
  table operation can be dispatched twice.

## 2. Replace inline payloads and pod-log results

- Store the canonical request in a bounded dispatch-payload record or object
  store; pass only a dispatch ID and short-lived retrieval credential to the
  pod.
- Keep Kubernetes Secrets limited to bootstrap credentials. Do not place S3 or
  catalog credentials in Job/Pod specs or annotations.
- Have the pod persist a versioned per-table result document before exit,
  including operation audits, checkpoint IDs, errors, and resource usage.
- Make the watcher finalize from the durable result. Pod logs should be
  diagnostic only, not the source of truth.
- Enforce payload/result size limits and reject or split an oversized packed
  dispatch before Job creation.
- Add recovery tests where the Job TTL expires and where the Governor is down
  longer than the TTL.

## 3. Make finalization and auditing idempotent

- Add a stable operation-attempt ID to each table operation.
- Add a uniqueness constraint to audit writes using that attempt ID.
- Update member status, dispatch status, operation audit, and run rollup in one
  database transaction.
- Release Redis table locks only after the terminal transaction commits.
- Reconcile leaked locks from terminal dispatch rows and renew locks while a
  Job is active.
- Add repeated-watcher and crash-during-finalization tests proving that retries
  neither duplicate audits nor lose terminal status.

## 4. Complete cancellation semantics

- Expose cancellation as asynchronous (`cancel_requested`) until Kubernetes
  confirms foreground deletion and all pods are gone.
- Keep table locks held while termination is in progress.
- Add a cancellation timeout and operator-visible `cancel_failed` state.
- Make the pod check a cancellation token between table operations so a shared
  pod does not start the next table after cancellation.
- Add tests for cancellation during create, pending, running, partial batch
  completion, Governor restart, and Kubernetes API outage.

## 5. Make placement and capacity namespace-aware

- Define one supported namespace policy: release namespace only, or explicit
  per-destination namespaces with corresponding RoleBindings.
- Persist namespace, image digest, service account, and resource request on the
  dispatch as immutable execution metadata.
- Count active Jobs from every allowed namespace and fail closed when live
  capacity cannot be read for a destination using direct Kubernetes dispatch.
- Use image digests for runtime Jobs so a retry cannot execute different code
  under the same dispatch.
- Add Helm integration tests in a non-`default` namespace and capacity tests
  with multiple destinations and concurrent Jobs.

## 6. Unify compaction group memory and file-count budgets

- Define one rewrite-group budget across compressed bytes, input-file count,
  decoder expansion, scan concurrency, and expected open file descriptors.
- Keep split and coalesce under both `max_bytes_per_group` and
  `max_files_per_group`; no later stage may undo an earlier safety cap.
- Add a conservative default file-count ceiling for tiny-file storms instead
  of leaving groups unbounded by file count when the override is absent.
- Derive the compressed-byte cap from measured expansion and the in-process
  memory budget rather than a fixed budget multiplier alone.
  **Done (2026-07-20):** planning expansion 8 in `resources.max_bytes_per_group_for_pod_mem`
  + Go `clampMaxBytesToMemoryBudget` (regression: run `3483a157` 2 GiB groups → ~20 GiB RSS).
- Benchmark dense Parquet and tiny-file workloads at every supported pod size;
  assert peak RSS and file descriptors remain below limits.

## Completion criteria

- Fault-injection tests cover every state transition.
- A Governor restart cannot create duplicate table maintenance.
- A completed Job can be finalized without its pod or logs.
- Cancellation never releases a table lock while a maintenance pod can still
  commit.
- Every operation has exactly one durable audit outcome.
- No long-lived catalog/storage secret appears in a Job or Pod specification.
