#!/usr/bin/env bash
# Start the local Cryo stack: Docker infra + API, web UI, worker, and governor.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PID_DIR="${PID_DIR:-$ROOT_DIR/.local/pids}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/.local/logs}"
NPM="${NPM:-npm}"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-5173}"

SEED_CONFIG="${SEED_CONFIG:-1}"
SEED_TABLES="${SEED_TABLES:-0}"
SEED_FIXTURES="${SEED_FIXTURES:-0}"
SEED_HMS_FIXTURES="${SEED_HMS_FIXTURES:-0}"
START_WEB="${START_WEB:-1}"
START_SERVE="${START_SERVE:-1}"
START_WORKER="${START_WORKER:-0}"
START_GOVERNOR="${START_GOVERNOR:-0}"
START_SCHEDULER="${START_SCHEDULER:-0}"
START_EVENTS="${START_EVENTS:-0}"
START_DATALAKE="${START_DATALAKE:-1}"
START_OBSERVABILITY="${START_OBSERVABILITY:-1}"
START_CRYO_DOCKER="${START_CRYO_DOCKER:-0}"
DOCKER_CRYO_SERVICES="${DOCKER_CRYO_SERVICES:-postgres redis}"
DOCKER_DATALAKE_SERVICES="${DOCKER_DATALAKE_SERVICES:-minio minio-init iceberg-rest hms-postgres hive-metastore trino}"
DOCKER_OBS_SERVICES="${DOCKER_OBS_SERVICES:-prometheus grafana loki promtail}"
DOCKER_DOWN_ON_STOP="${DOCKER_DOWN_ON_STOP:-0}"
RESET_LOCAL_POSTGRES="${RESET_LOCAL_POSTGRES:-0}"

COMPOSE_CRYO_FILE="docker-compose.yml"
COMPOSE_DATALAKE_FILE="docker-compose.dev-datalake.yml"
COMPOSE_OBS_FILE="docker-compose.observability.yml"

log() {
  printf '==> %s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./start-local.sh [command]

Commands:
  start     Start Docker infra and Cryo app processes (default)
  stop      Stop Cryo app processes (optionally Docker with DOCKER_DOWN_ON_STOP=1)
  status    Show Docker and Cryo process status
  logs      Tail Cryo app logs (api, web, worker, governor, scheduler)

Environment:
  SEED_CONFIG=1          Upsert local source/destination/preset rows
  SEED_TABLES=0          Seed demo Iceberg tables after Docker is up
  SEED_FIXTURES=0        Also seed fragmented fixture tables
  SEED_HMS_FIXTURES=0    Seed HMS fixture tables (large; opt-in)
  START_WEB=1            Start Vite dev server
  START_SERVE=1         Start governor + worker + scheduler via cryo serve (default 1)
  START_WORKER=0        Start maintenance worker alone (ignored when START_SERVE=1)
  START_GOVERNOR=0      Start load-aware governor alone (ignored when START_SERVE=1)
  START_SCHEDULER=0     Start scheduler alone (ignored when START_SERVE=1)
  START_EVENTS=0         Start event watcher + consumer (Phase 4 fast-path)
  START_DATALAKE=1       Start MinIO, catalogs, Trino (dev datalake compose file)
  START_OBSERVABILITY=1  Start Prometheus, Grafana, Loki, Promtail
  START_CRYO_DOCKER=0    Start cryo-* containers (default: host-run API + cryo serve)
  DOCKER_CRYO_SERVICES=postgres redis
  DOCKER_DATALAKE_SERVICES=minio minio-init iceberg-rest hms-postgres hive-metastore trino
  DOCKER_OBS_SERVICES=prometheus grafana loki promtail
  DOCKER_DOWN_ON_STOP=0  Stop Docker stack when running ./start-local.sh stop
  RESET_LOCAL_POSTGRES=0 Recreate local Postgres volume if credentials are stale
  API_PORT=8000          FastAPI port
  WEB_PORT=5173          Vite dev server port
EOF
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
  docker compose version >/dev/null 2>&1 || die "docker compose is not available"
}

setup_compose_env() {
  export COMPOSE_FILE="${COMPOSE_CRYO_FILE}:${COMPOSE_DATALAKE_FILE}:${COMPOSE_OBS_FILE}"
}

compose_cryo() {
  docker compose -f "$COMPOSE_CRYO_FILE" "$@"
}

compose_datalake() {
  docker compose -f "$COMPOSE_DATALAKE_FILE" "$@"
}

compose_obs() {
  docker compose -f "$COMPOSE_OBS_FILE" "$@"
}

compose_all() {
  setup_compose_env
  docker compose "$@"
}

ensure_dirs() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
}

ensure_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "$PYTHON_BIN not found; install Python 3.12 or set PYTHON_BIN"
    log "Creating virtual environment at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  if ! python -c "
import cryo, uvicorn, prometheus_client
from cryo.infra.wiring import build_runtime_wiring
" >/dev/null 2>&1; then
    log "Installing backend dependencies"
    python -m pip install --upgrade pip -q
    python -m pip install -e ".[dev]" -q
  fi
}

ensure_web_deps() {
  if [[ ! -d "$ROOT_DIR/web/node_modules" ]]; then
    log "Installing frontend dependencies"
    "$NPM" --prefix "$ROOT_DIR/web" install
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-60}"

  log "Waiting for $name at $url"
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name is ready"
      return 0
    fi
    sleep 2
  done

  die "$name did not become ready in time ($url)"
}

wait_for_container_health() {
  local service="$1"
  local name="$2"
  local attempts="${3:-60}"
  local container_id=""
  local status=""

  log "Waiting for $name container health"
  for ((i = 1; i <= attempts; i++)); do
    container_id="$(compose_all ps -q "$service")"
    if [[ -n "$container_id" ]]; then
      status="$(
        docker inspect \
          --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
          "$container_id" 2>/dev/null || true
      )"
      if [[ "$status" == "healthy" || "$status" == "running" ]]; then
        log "$name is ready"
        return 0
      fi
    fi
    sleep 2
  done

  die "$name did not become ready in time"
}

start_docker() {
  setup_compose_env

  if [[ "$START_DATALAKE" == "1" ]]; then
    log "Starting dev datalake services: $DOCKER_DATALAKE_SERVICES"
    # shellcheck disable=SC2086
    compose_datalake up -d $DOCKER_DATALAKE_SERVICES
  fi

  if [[ "$START_CRYO_DOCKER" == "1" ]]; then
    log "Starting Cryo Docker services: $DOCKER_CRYO_SERVICES"
    # shellcheck disable=SC2086
    compose_cryo up -d $DOCKER_CRYO_SERVICES
  else
    log "Starting Cryo dependencies: $DOCKER_CRYO_SERVICES"
    # shellcheck disable=SC2086
    compose_cryo up -d $DOCKER_CRYO_SERVICES
  fi

  if [[ "$START_OBSERVABILITY" == "1" ]]; then
    log "Starting observability services: $DOCKER_OBS_SERVICES"
    # shellcheck disable=SC2086
    compose_obs up -d $DOCKER_OBS_SERVICES
  fi

  if [[ "$START_DATALAKE" == "1" ]]; then
    wait_for_url "MinIO" "http://localhost:9000/minio/health/live"
    wait_for_url "Iceberg REST catalog" "http://localhost:8181/v1/config"
    wait_for_url "Trino" "http://localhost:8080/v1/info"
    wait_for_container_health "hive-metastore" "Hive Metastore"
  fi

  wait_for_container_health "postgres" "Postgres"
  wait_for_container_health "redis" "Redis"

  if [[ "$START_OBSERVABILITY" == "1" ]]; then
    wait_for_url "Prometheus" "http://localhost:9090/-/ready" 30
    wait_for_url "Grafana" "http://localhost:3000/api/health" 30
  fi
}

ensure_postgres_auth() {
  log "Verifying Postgres credentials"
  if python - <<'PY' >/dev/null 2>&1
import psycopg

with psycopg.connect("postgresql://cryo:cryo@127.0.0.1:5432/cryo") as conn:
    with conn.cursor() as cur:
        cur.execute("select 1")
PY
  then
    return 0
  fi

  if [[ "$RESET_LOCAL_POSTGRES" != "1" ]]; then
    die "Postgres rejected cryo/cryo. If this is an old local volume, rerun with RESET_LOCAL_POSTGRES=1 ./start-local.sh start"
  fi

  warn "Postgres password mismatch; recreating postgres volume for local dev (cryo/cryo)"
  compose_cryo stop postgres >/dev/null 2>&1 || true
  compose_cryo rm -f postgres >/dev/null 2>&1 || true
  docker volume rm -f cryo_postgres_data >/dev/null 2>&1 || true
  docker volume rm -f iceberg_compactor_postgres_data >/dev/null 2>&1 || true
  compose_cryo up -d postgres
  wait_for_container_health "postgres" "Postgres"

  if ! python - <<'PY' >/dev/null 2>&1
import psycopg

with psycopg.connect("postgresql://cryo:cryo@127.0.0.1:5432/cryo") as conn:
    with conn.cursor() as cur:
        cur.execute("select 1")
PY
  then
    die "Postgres still unreachable with cryo/cryo after volume reset"
  fi
}

migrate_db() {
  log "Running database migrations"
  alembic upgrade head
}

seed_config() {
  if [[ "$SEED_CONFIG" != "1" ]]; then
    log "Skipping Cryo config seed (SEED_CONFIG=$SEED_CONFIG)"
    return 0
  fi

  log "Seeding local Cryo source, destination, and preset"
  PYTHONPATH="$ROOT_DIR" python "$ROOT_DIR/scripts/seed_local_config.py"
}

seed_tables() {
  if [[ "$SEED_TABLES" != "1" ]]; then
    log "Skipping Iceberg table seed (SEED_TABLES=$SEED_TABLES)"
    return 0
  fi

  log "Seeding demo Iceberg tables"
  python "$ROOT_DIR/scripts/create_test_tables.py"

  if [[ "$SEED_FIXTURES" == "1" ]]; then
    log "Seeding fragmented partition fixture tables"
    python "$ROOT_DIR/scripts/create_partition_fixture_tables.py"
  fi
}

seed_hms_fixtures() {
  if [[ "$SEED_HMS_FIXTURES" != "1" ]]; then
    log "Skipping HMS fixture seed (SEED_HMS_FIXTURES=$SEED_HMS_FIXTURES)"
    return 0
  fi

  log "Seeding HMS fixture tables"
  HMS_FIXTURE_SCALE="${HMS_FIXTURE_SCALE:-smoke}" \
    python "$ROOT_DIR/tests/integration/hms/generate_hms_fixture_tables.py"
}

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start_process() {
  local name="$1"
  local required="${2:-1}"
  shift 2
  local pid_file="$PID_DIR/${name}.pid"
  local log_file="$LOG_DIR/${name}.log"

  if is_running "$pid_file"; then
    log "$name already running (pid $(cat "$pid_file"))"
    return 0
  fi

  : >"$log_file"
  # Detach from the launching shell (start_new_session) so processes survive
  # after start-local.sh exits.
  local daemon_pid
  daemon_pid="$(
    "$VENV_DIR/bin/python" - "$ROOT_DIR" "$VENV_DIR" "$log_file" "$@" <<'PY'
import os
import subprocess
import sys

root_dir, venv_dir, log_file = sys.argv[1:4]
command = sys.argv[4:]
env = os.environ.copy()
env["VIRTUAL_ENV"] = venv_dir
env["PATH"] = f"{venv_dir}/bin:{env.get('PATH', '')}"
with open(log_file, "a", encoding="utf-8") as log_handle:
    proc = subprocess.Popen(
        command,
        cwd=root_dir,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
print(proc.pid)
PY
  )"
  echo "$daemon_pid" >"$pid_file"
  sleep 1

  if ! is_running "$pid_file"; then
    rm -f "$pid_file"
    if [[ "$required" == "1" ]]; then
      warn "$name failed to start; see $log_file"
      tail -n 20 "$log_file" >&2 || true
      return 1
    fi
    warn "$name is unavailable; see $log_file"
    return 0
  fi

  log "Started $name (pid $(cat "$pid_file"), log $log_file)"
}

start_app_processes() {
  start_process api 1 \
    uvicorn cryo.api.app:create_app --factory --reload \
    --reload-dir cryo --reload-exclude '.local/*' \
    --host "$API_HOST" --port "$API_PORT"

  if [[ "$START_WEB" == "1" ]]; then
    ensure_web_deps
    start_process web 1 \
      "$NPM" --prefix "$ROOT_DIR/web" run dev -- --host "$WEB_HOST" --port "$WEB_PORT"
  fi

  if [[ "$START_SERVE" == "1" ]]; then
    start_process serve 1 python -m cryo.cli serve
  else
    if [[ "$START_WORKER" == "1" ]]; then
      start_process worker 0 python -m cryo.cli worker
    fi

    if [[ "$START_GOVERNOR" == "1" ]]; then
      start_process governor 0 python -m cryo.cli governor
    fi

    if [[ "$START_SCHEDULER" == "1" ]]; then
      start_process scheduler 0 python -m cryo.cli scheduler
    fi
  fi

  if [[ "$START_EVENTS" == "1" ]]; then
    start_process events 0 python -m cryo.cli events
  fi
}

verify_background_processes() {
  if [[ "$START_SERVE" == "1" ]] && ! is_running "$PID_DIR/serve.pid"; then
    die "cryo serve exited during startup; see $LOG_DIR/serve.log"
  fi
}

wait_for_app() {
  wait_for_url "Cryo API" "http://${API_HOST}:${API_PORT}/docs" 30
  if [[ "$START_WEB" == "1" ]]; then
    wait_for_url "Cryo web UI" "http://${WEB_HOST}:${WEB_PORT}/" 30
  fi
}

print_summary() {
  cat <<EOF

Cryo local stack is running.

Application:
  Web UI     http://${WEB_HOST}:${WEB_PORT}/
  API        http://${API_HOST}:${API_PORT}/
  API docs   http://${API_HOST}:${API_PORT}/docs
EOF

  if [[ "$START_SERVE" == "1" ]]; then
    cat <<EOF
  Orchestration (cryo serve — governor, worker, scheduler, scanner)
             log: $LOG_DIR/serve.log
EOF
  fi

  cat <<EOF

Infrastructure:
EOF

  if [[ "$START_DATALAKE" == "1" ]]; then
    cat <<EOF
  MinIO API       http://localhost:9000
  MinIO console   http://localhost:9001  (minioadmin / minioadmin)
  Iceberg REST    http://localhost:8181
  Hive Metastore  thrift://localhost:9083
  Trino           http://localhost:8080
EOF
  fi

  cat <<EOF
  Postgres        localhost:5432  (cryo / cryo / cryo)
  Redis           localhost:6379
EOF

  if [[ "$START_OBSERVABILITY" == "1" ]]; then
    cat <<EOF
  Prometheus      http://localhost:9090
  Grafana         http://localhost:3000  (admin / admin)
  Loki            http://localhost:3100
EOF
  fi

  cat <<EOF

Useful commands:
  ./start-local.sh status
  ./start-local.sh logs              # tails api, web, serve, ...
  ./start-local.sh stop
  make scan
  SEED_TABLES=1 SEED_FIXTURES=1 ./start-local.sh start

Orchestration is started via cryo serve (same as make serve); no separate terminal needed.

Local API auth header:
  Authorization: Bearer admin
EOF
}

stop_process() {
  local name="$1"
  local pid_file="$PID_DIR/${name}.pid"

  if ! is_running "$pid_file"; then
    rm -f "$pid_file"
    return 0
  fi

  local pid
  pid="$(cat "$pid_file")"
  log "Stopping $name (pid $pid)"
  kill "$pid" 2>/dev/null || true

  for _ in $(seq 1 10); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      return 0
    fi
    sleep 0.5
  done

  warn "Force stopping $name (pid $pid)"
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pid_file"
}

stop_app_processes() {
  stop_process events
  stop_process serve
  stop_process scheduler
  stop_process governor
  stop_process worker
  stop_process web
  stop_process api
}

cmd_status() {
  require_docker
  ensure_dirs
  setup_compose_env

  log "Docker services"
  compose_all ps

  printf '\n'
  log "Cryo app processes"
  local name
  for name in api web serve worker governor scheduler events; do
    local pid_file="$PID_DIR/${name}.pid"
    if is_running "$pid_file"; then
      printf '  %-10s running (pid %s, log %s/%s.log)\n' "$name" "$(cat "$pid_file")" "$LOG_DIR" "$name"
    else
      printf '  %-10s stopped\n' "$name"
    fi
  done
}

cmd_logs() {
  ensure_dirs
  if compgen -G "$LOG_DIR/*.log" >/dev/null; then
    tail -f "$LOG_DIR"/*.log
  else
    die "No logs found in $LOG_DIR (start the stack first)"
  fi
}

cmd_start() {
  require_docker
  ensure_dirs
  ensure_venv
  start_docker
  ensure_postgres_auth
  migrate_db
  seed_config
  seed_tables
  seed_hms_fixtures
  start_app_processes
  verify_background_processes
  wait_for_app
  print_summary
}

cmd_stop() {
  ensure_dirs
  stop_app_processes

  if [[ "$DOCKER_DOWN_ON_STOP" == "1" ]]; then
    require_docker
    setup_compose_env
    log "Stopping Docker services"
    compose_obs down >/dev/null 2>&1 || true
    compose_cryo down >/dev/null 2>&1 || true
    compose_datalake down >/dev/null 2>&1 || true
  else
    log "Docker services left running (set DOCKER_DOWN_ON_STOP=1 to stop them)"
  fi
}

main() {
  local command="${1:-start}"
  case "$command" in
    start)
      cmd_start
      ;;
    stop)
      cmd_stop
      ;;
    status)
      cmd_status
      ;;
    logs)
      cmd_logs
      ;;
    -h | --help | help)
      usage
      ;;
    *)
      die "Unknown command: $command (try ./start-local.sh --help)"
      ;;
  esac
}

main "$@"
