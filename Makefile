.PHONY: install lint format typecheck test migrate api scan worker governor scheduler up down ps logs seed seed-docker seed-fragmented smoke-file-counts smoke-compact spark-shell web-install web-dev web-build web-test web-gen start-local stop-local status-local

PYTHON ?= python3.12
SPARK_RUNNER = docker compose run --rm spark
NPM ?= npm

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check cryo tests
	ruff format --check cryo tests

format:
	ruff format cryo tests
	ruff check --fix cryo tests

typecheck:
	mypy cryo

test:
	pytest

migrate:
	alembic upgrade head

api:
	uvicorn cryo.api.app:create_app --factory --reload

scan:
	$(PYTHON) -m cryo.scanner.run

worker:
	$(PYTHON) -m cryo.worker.worker

governor:
	$(PYTHON) -m cryo.governor.governor

scheduler:
	$(PYTHON) -m cryo.scheduler.scheduler

up:
	docker compose up -d

down:
	docker compose down

seed:
	$(PYTHON) scripts/create_test_tables.py

seed-config:
	PYTHONPATH=. $(PYTHON) scripts/seed_local_config.py

ps:
	docker compose ps

logs:
	docker compose logs -f

seed-docker: up
	$(SPARK_RUNNER) python3 scripts/create_test_tables.py

seed-fragmented: up
	$(SPARK_RUNNER) python3 scripts/create_partition_fixture_tables.py

smoke-file-counts: up
	$(SPARK_RUNNER) python3 scripts/smoke_file_counts.py

smoke-compact: up
	$(SPARK_RUNNER) python3 scripts/smoke_file_counts.py --compact --assert-drop

spark-shell: up
	docker compose exec spark bash

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
