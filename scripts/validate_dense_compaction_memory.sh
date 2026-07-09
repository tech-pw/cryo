#!/usr/bin/env bash
# Validate dense-partition native compaction under a 1 GiB cgroup memory limit.
# Reproduces the GeoIP OOM class (run 858ad9eb) with the synthetic f_part_dense fixture.
#
# Usage (from repo root):
#   make up-datalake-native
#   make seed-native-compaction-fixtures
#   make validate-dense-compaction-memory
#
# Requires: docker, curl, python3 with cryo deps (or .venv).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENGINE_BIN="${CRYO_ENGINE_BINARY:-}"
if [[ -z "$ENGINE_BIN" ]]; then
  if [[ -x "$ROOT/engine-go/bin/cryo-engine-go-linux" ]]; then
    ENGINE_BIN="$ROOT/engine-go/bin/cryo-engine-go-linux"
  else
    ENGINE_BIN="$ROOT/engine-go/bin/cryo-engine-go"
  fi
fi
REST_URI="${CRYO_REST_URI:-http://localhost:8181}"
MEMORY_LIMIT="${CRYO_DENSE_MEM_LIMIT:-1g}"
TABLE_REF="${CRYO_DENSE_TABLE:-native_compaction.f_part_dense}"
REQUEST_JSON="${TMPDIR:-/tmp}/cryo-dense-compaction-request.json"
STATS_LOG="${TMPDIR:-/tmp}/cryo-dense-compaction-stats.log"
ENGINE_LOG="${TMPDIR:-/tmp}/cryo-dense-compaction-engine.log"

if [[ ! -x "$ENGINE_BIN" && ! -f "$ENGINE_BIN" ]]; then
  echo "error: engine binary not found at $ENGINE_BIN (run make build-engine-go-linux)" >&2
  exit 1
fi
# Linux cross-build may not have +x on some filesystems; chmod for the mount.
chmod +x "$ENGINE_BIN" 2>/dev/null || true

if ! curl -sf "${REST_URI}/v1/config" >/dev/null; then
  echo "error: REST catalog not reachable at $REST_URI (run make up-datalake-native)" >&2
  exit 1
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
else
  PYTHON="${PYTHON:-python3}"
fi

REWRITE_HOST=0
DOCKER_NETWORK_ARGS=(--network host)
if [[ "$(uname -s)" == "Darwin" ]]; then
  # Docker Desktop Mac: use host.docker.internal instead of --network host.
  REWRITE_HOST=1
  DOCKER_NETWORK_ARGS=(--add-host=host.docker.internal:host-gateway)
fi

echo "==> Building execute request for $TABLE_REF"
export TABLE_REF REQUEST_JSON REWRITE_HOST
"$PYTHON" - <<'PY'
import json
import os

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from tests.integration.native.rest_env import source_config

plan = MaintenancePlan(
    table_ref=os.environ["TABLE_REF"],
    mode=Mode.DEEP,
    operations=(
        Operation(
            OperationType.OPTIMIZE,
            {
                "target_file_size_bytes": 4 * 1024 * 1024,
                "min_file_size_bytes": 512 * 1024,
                "min_input_files": 3,
                "scan_concurrency": 1,
                "max_bytes_per_group": 32 * 1024 * 1024,
                "partial_progress": True,
            },
        ),
    ),
)
request = build_execute_request_for_operations(
    plan=plan,
    operations=plan.operations,
    source_config=source_config(),
    destination_config={"operation_mode": "execute"},
)

if os.environ.get("REWRITE_HOST") == "1":

    def fix(url: object) -> object:
        if not isinstance(url, str):
            return url
        return (
            url.replace("http://localhost:", "http://host.docker.internal:")
            .replace("http://127.0.0.1:", "http://host.docker.internal:")
        )

    for key in ("catalog_uri", "warehouse", "s3_endpoint"):
        if key in request:
            request[key] = fix(request[key])
    props = request.get("catalog_properties")
    if isinstance(props, dict):
        request["catalog_properties"] = {k: fix(v) for k, v in props.items()}

path = os.environ["REQUEST_JSON"]
with open(path, "w") as f:
    json.dump(request, f)
print("wrote", path)
PY

CONTAINER_NAME="cryo-dense-compaction-$$"
echo "==> Running cryo-engine-go under --memory=${MEMORY_LIMIT} (container=$CONTAINER_NAME)"
rm -f "$STATS_LOG" "$ENGINE_LOG"

(
  while docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; do
    docker stats --no-stream --format \
      "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" \
      "$CONTAINER_NAME" >>"$STATS_LOG" 2>/dev/null || true
    sleep 1
  done
) &
STATS_PID=$!

set +e
# Prefer a glibc-based image so a static Go binary runs without musl quirks.
# busybox provides a shell for piping stdin.
docker run --rm --name "$CONTAINER_NAME" \
  --memory="$MEMORY_LIMIT" --memory-swap="$MEMORY_LIMIT" \
  "${DOCKER_NETWORK_ARGS[@]}" \
  -v "$ENGINE_BIN:/engine/cryo-engine-go:ro" \
  -v "$REQUEST_JSON:/tmp/request.json:ro" \
  --entrypoint /bin/sh \
  public.ecr.aws/docker/library/debian:bookworm-slim \
  -c 'cat /tmp/request.json | /engine/cryo-engine-go execute' \
  >"$ENGINE_LOG" 2>&1
EXIT=$?
set -e

kill "$STATS_PID" 2>/dev/null || true
wait "$STATS_PID" 2>/dev/null || true

echo "==> Engine exit code: $EXIT"
echo "==> Last docker stats samples:"
tail -n 20 "$STATS_LOG" 2>/dev/null || echo "(no stats captured)"
echo "==> Engine log (progress + response):"
grep -E 'CRYO_PROGRESS:|"ok"|OOM|error|group_started|checkpoint' "$ENGINE_LOG" | tail -n 80 || true
echo "(full log: $ENGINE_LOG)"

if [[ $EXIT -eq 137 ]]; then
  echo "FAIL: container OOM-killed (exit 137) under ${MEMORY_LIMIT}" >&2
  exit 1
fi
if [[ $EXIT -ne 0 ]]; then
  echo "FAIL: engine exited $EXIT" >&2
  tail -n 40 "$ENGINE_LOG" >&2
  exit 1
fi
if ! grep -q '"ok": *true\|"ok":true' "$ENGINE_LOG"; then
  echo "FAIL: no ok:true in engine stdout" >&2
  exit 1
fi
if ! grep -q 'group_started\|checkpoint' "$ENGINE_LOG"; then
  echo "WARN: no group_started/checkpoint progress lines (partial_progress may be off)" >&2
fi

echo "OK: dense compaction completed under ${MEMORY_LIMIT}"
