#!/usr/bin/env bash
# Start Docker services (MinIO, Iceberg REST, Trino, Postgres, Redis, Spark) and launch JupyterLab locally.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
JUPYTER_PORT="${JUPYTER_PORT:-8888}"
JUPYTER_IP="${JUPYTER_IP:-127.0.0.1}"
SEED_TABLES="${SEED_TABLES:-1}"
SEED_FIXTURES="${SEED_FIXTURES:-0}"

log() {
  printf '==> %s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
docker compose version >/dev/null 2>&1 || die "docker compose is not available"

ensure_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "$PYTHON_BIN not found; install Python 3.12 or set PYTHON_BIN"
    log "Creating virtual environment at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  if ! python -c "import jupyterlab, pyspark" >/dev/null 2>&1; then
    log "Installing Python dependencies"
    pip install --upgrade pip setuptools -q
    pip install -r requirements.txt -q
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
    container_id="$(docker compose ps -q "$service")"
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

start_services() {
  log "Starting Docker services"
  docker compose up -d

  wait_for_url "MinIO" "http://localhost:9000/minio/health/live"
  wait_for_url "Iceberg REST catalog" "http://localhost:8181/v1/config"
  wait_for_url "Trino" "http://localhost:8090/v1/info"
  wait_for_container_health "postgres" "Postgres"
  wait_for_container_health "redis" "Redis"
  wait_for_container_health "spark" "Spark runner"
}

seed_demo_tables() {
  if [[ "$SEED_TABLES" != "1" ]]; then
    log "Skipping table seed (SEED_TABLES=$SEED_TABLES)"
    return 0
  fi

  log "Seeding demo Iceberg tables"
  python scripts/create_test_tables.py

  if [[ "$SEED_FIXTURES" == "1" ]]; then
    log "Seeding fragmented partition fixture tables"
    python scripts/create_partition_fixture_tables.py
  fi
}

start_jupyter() {
  log "Starting JupyterLab at http://${JUPYTER_IP}:${JUPYTER_PORT}"
  log "Open the URL printed below in your browser"
  log "Docker services will keep running after you stop Jupyter (Ctrl+C)"

  exec jupyter lab \
    --ip="$JUPYTER_IP" \
    --port="$JUPYTER_PORT" \
    --no-browser \
    --notebook-dir="$ROOT_DIR/notebooks" \
    notebooks/iceberg_local_demo.ipynb
}

main() {
  ensure_venv
  start_services
  seed_demo_tables
  start_jupyter
}

main "$@"
