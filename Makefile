.PHONY: install lint format typecheck test migrate migrate-local api scan scanner worker governor scheduler report-scheduler daily-report up up-cryo up-datalake down ps logs seed seed-docker seed-fragmented seed-hms-fixtures seed-hms-fixtures-docker seed-native-fixtures seed-native-compaction-fixtures test-hms-integration test-native-e2e test-native-e2e-go test-native-e2e-local test-native-e2e-local-go test-native-compaction-e2e test-native-compaction-e2e-go test-native-compaction-e2e-local test-native-compaction-e2e-local-go test-native-compaction-perf test-native-compaction-perf-go build-engine-go build-seed-native-fixtures test-engine-go smoke-hms-trino smoke-file-counts smoke-compact spark-shell load-test-data load-test-data-docker web-install web-dev web-build web-test web-gen start-local stop-local status-local

PYTHON ?= python3.12
TRINO_VERSION ?= 480
export TRINO_VERSION
COMPOSE_CRYO = docker compose -f docker-compose.yml
COMPOSE_DATALAKE = docker compose -f docker-compose.dev-datalake.yml
COMPOSE_LOCAL = docker compose -f docker-compose.yml -f docker-compose.dev-datalake.yml -f docker-compose.observability.yml
SPARK_RUNNER = $(COMPOSE_DATALAKE) --profile tools run --rm spark
NPM ?= npm

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check cryo tests --extend-exclude "tests/integration/native"
	ruff format --check cryo tests --extend-exclude "tests/integration/native"

format:
	ruff format cryo tests
	ruff check --fix cryo tests

typecheck:
	mypy cryo

test:
	pytest

migrate:
	alembic upgrade head

migrate-local:
	./start-local.sh migrate

api:
	uvicorn cryo.api.app:create_app --factory --reload

scan:
	$(PYTHON) -m cryo.scanner.run

scanner:
	$(PYTHON) -m cryo.cli scanner

worker:
	$(PYTHON) -m cryo.cli worker

governor:
	$(PYTHON) -m cryo.cli governor

scheduler:
	$(PYTHON) -m cryo.cli scheduler

report-scheduler:
	$(PYTHON) -m cryo.reporting.scheduler

daily-report:
	$(PYTHON) -c "import asyncio; from cryo.reporting.scheduler import run_daily_report_now; print(asyncio.run(run_daily_report_now()))"

serve:
	$(PYTHON) -m cryo.cli serve

up:
	$(COMPOSE_LOCAL) up -d

up-cryo:
	$(COMPOSE_CRYO) up -d

up-datalake:
	$(COMPOSE_DATALAKE) up -d

down:
	$(COMPOSE_LOCAL) down

seed:
	$(PYTHON) scripts/create_test_tables.py

seed-config:
	PYTHONPATH=. $(PYTHON) scripts/seed_local_config.py

ps:
	$(COMPOSE_LOCAL) ps

logs:
	$(COMPOSE_LOCAL) logs -f

seed-docker: up-datalake
	$(SPARK_RUNNER) python3 scripts/create_test_tables.py

seed-fragmented: up-datalake
	$(SPARK_RUNNER) python3 scripts/create_partition_fixture_tables.py

seed-hms-fixtures:
	HMS_FIXTURE_SCALE=$${HMS_FIXTURE_SCALE:-smoke} $(PYTHON) tests/integration/hms/generate_hms_fixture_tables.py

seed-hms-fixtures-docker: up-datalake
	$(COMPOSE_DATALAKE) --profile tools run --rm --entrypoint python3 spark \
	  tests/integration/hms/generate_hms_fixture_tables.py

test-hms-integration:
	CRYO_HMS_URI=$${CRYO_HMS_URI:-thrift://localhost:9083} pytest tests/integration/hms/test_hms_catalog_integration.py -v

seed-native-fixtures:
	$(PYTHON) tests/integration/native/generate_rest_fixture.py

seed-native-compaction-fixtures: build-engine-go build-seed-native-fixtures
	CRYO_SEED_NATIVE_FIXTURES_BINARY=engine-go/bin/seed-native-fixtures \
	  $(PYTHON) tests/integration/native/generate_compaction_fixtures.py

build-seed-native-fixtures:
	cd engine-go && CGO_ENABLED=0 go build -o bin/seed-native-fixtures ./cmd/seed-native-fixtures

test-native-e2e:
	CRYO_REST_URI=$${CRYO_REST_URI:-http://localhost:8181} \
	  pytest tests/integration/native/test_native_expire_orphan_e2e.py -v

build-engine-go:
	cd engine-go && CGO_ENABLED=0 go build -o bin/cryo-engine-go ./cmd/cryo-engine-go

test-engine-go:
	cd engine-go && CGO_ENABLED=0 go test ./...

test-native-e2e-go: build-engine-go
	CRYO_REST_URI=$${CRYO_REST_URI:-http://localhost:8181} \
	CRYO_ENGINE_IMPL=go \
	  $(PYTHON) -m pytest tests/integration/native/test_native_expire_orphan_e2e.py -v

test-native-e2e-local: up-datalake-native seed-native-fixtures test-native-e2e

test-native-e2e-local-go: up-datalake-native seed-native-fixtures test-native-e2e-go

test-native-compaction-e2e:
	CRYO_REST_URI=$${CRYO_REST_URI:-http://localhost:8181} \
	  pytest tests/integration/native/test_native_compaction_e2e.py -v

test-native-compaction-e2e-go: build-engine-go build-seed-native-fixtures
	CRYO_REST_URI=$${CRYO_REST_URI:-http://localhost:8181} \
	CRYO_ENGINE_IMPL=go \
	CRYO_ENGINE_BINARY=$${CRYO_ENGINE_BINARY:-engine-go/bin/cryo-engine-go} \
	CRYO_SEED_NATIVE_FIXTURES_BINARY=$${CRYO_SEED_NATIVE_FIXTURES_BINARY:-engine-go/bin/seed-native-fixtures} \
	  $(PYTHON) -m pytest tests/integration/native/test_native_compaction_e2e.py tests/integration/native/test_native_mor_compaction_e2e.py tests/integration/native/test_native_mor_concurrency_e2e.py tests/integration/native/test_native_fault_injection_e2e.py -v

test-native-differential-e2e-go: build-engine-go build-seed-native-fixtures
	CRYO_REST_URI=$${CRYO_REST_URI:-http://localhost:8181} \
	CRYO_TRINO_URI=$${CRYO_TRINO_URI:-http://localhost:8080} \
	CRYO_ENGINE_IMPL=go \
	CRYO_ENGINE_BINARY=$${CRYO_ENGINE_BINARY:-engine-go/bin/cryo-engine-go} \
	CRYO_SEED_NATIVE_FIXTURES_BINARY=$${CRYO_SEED_NATIVE_FIXTURES_BINARY:-engine-go/bin/seed-native-fixtures} \
	  $(PYTHON) -m pytest tests/integration/native/test_native_differential_e2e.py tests/integration/native/test_native_mor_differential_e2e.py -v

test-native-compaction-e2e-local: up-datalake-native seed-native-compaction-fixtures test-native-compaction-e2e

test-native-compaction-e2e-local-go: up-datalake-native seed-native-compaction-fixtures test-native-compaction-e2e-go

test-native-compaction-perf:
	pytest tests/integration/native/test_native_compaction_perf.py -m integration

test-native-compaction-perf-go: build-engine-go
	CRYO_ENGINE_IMPL=go \
	CRYO_ENGINE_BINARY=$${CRYO_ENGINE_BINARY:-engine-go/bin/cryo-engine-go} \
	pytest tests/integration/native/test_native_compaction_perf.py -m integration

up-datalake-native:
	$(COMPOSE_DATALAKE) up -d minio minio-init iceberg-rest

smoke-hms-trino: up-datalake
	$(PYTHON) tests/integration/hms/smoke_hms_trino.py

smoke-file-counts: up-datalake
	$(SPARK_RUNNER) python3 scripts/smoke_file_counts.py

smoke-compact: up-datalake
	$(SPARK_RUNNER) python3 scripts/smoke_file_counts.py --compact --assert-drop

smoke-e2e:
	$(PYTHON) scripts/smoke_cryo_e2e.py

load-test-data:
	$(PYTHON) scripts/load_test_data.py $(ARGS)

load-test-data-docker:
	$(COMPOSE_DATALAKE) up -d minio minio-init iceberg-rest hive-metastore hms-postgres
	$(SPARK_RUNNER) python3 scripts/load_test_data.py $(ARGS)

spark-shell: up-datalake
	$(COMPOSE_DATALAKE) --profile tools up -d spark
	$(COMPOSE_DATALAKE) --profile tools exec spark bash

web-install:
	$(NPM) --prefix web install

web-dev:
	$(NPM) --prefix web run dev

web-build:
	$(NPM) --prefix web run build

web-test:
	$(NPM) --prefix web test

web-gen:
	$(NPM) --prefix web run gen:api

start-local:
	./start-local.sh start

stop-local:
	./start-local.sh stop

status-local:
	./start-local.sh status
