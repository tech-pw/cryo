"""scripts/demo.py drives the quickstart's `make demo` against the API; no Docker needed here."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from cryo.domain.enums import OperationType
from cryo.workflows.approvals import requires_approval
from scripts import demo
from scripts.seed_local_config import LOCAL_SOURCE, LOCAL_STANDARD_PRESET

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "11111111-1111-1111-1111-111111111111"
PRESET_ID = "22222222-2222-2222-2222-222222222222"
DESTINATION_ID = "33333333-3333-3333-3333-333333333333"
TABLE_IDS = ["44444444-4444-4444-4444-444444444444", "55555555-5555-5555-5555-555555555555"]

Handler = Callable[[dict[str, Any]], tuple[int, Any]]


class FakeApi:
    """Routes (method, path) to handlers and records every call with its decoded body."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.handlers: dict[tuple[str, str], Handler] = {}
        self.run_statuses: dict[str, list[str]] = {}
        self.graded = False
        self.on("GET", "/healthz", lambda _: (200, {"status": "ok"}))
        self.on("POST", "/api/v1/auth/token", lambda _: (200, {"access_token": "tok"}))
        self.on(
            "GET",
            "/api/v1/sources",
            lambda _: (200, [{"id": SOURCE_ID, "name": LOCAL_SOURCE["name"]}]),
        )
        self.on(
            "GET",
            "/api/v1/presets",
            lambda _: (
                200,
                [
                    {
                        "id": PRESET_ID,
                        "name": LOCAL_STANDARD_PRESET["name"],
                        "destination_id": DESTINATION_ID,
                        "mode": LOCAL_STANDARD_PRESET["mode"],
                        "operations": LOCAL_STANDARD_PRESET["operations"],
                        "paused": False,
                    }
                ],
            ),
        )
        self.on("POST", "/api/v1/scans", self._scan)
        self.on("GET", "/api/v1/tables", self._tables)
        self.on("POST", "/api/v1/assignments", lambda _: (200, {"affected": len(TABLE_IDS)}))
        self.on("POST", "/api/v1/maintenance/run", lambda _: (201, {"run_id": "run-1"}))
        self.on("GET", "/api/v1/runs", lambda _: (200, []))

    def on(self, method: str, path: str, handler: Handler) -> None:
        self.handlers[(method, path)] = handler

    def _scan(self, call: dict[str, Any]) -> tuple[int, Any]:
        run_id = f"scan-{sum(1 for c in self.calls if c['path'] == '/api/v1/scans')}"
        self.run_statuses.setdefault(run_id, ["running", "succeeded"])
        self.graded = True
        return 202, {"run_id": run_id, "queued": True, "message": "queued"}

    def _tables(self, call: dict[str, Any]) -> tuple[int, Any]:
        stats = {"grade": "D", "overall_score": 55, "small_files_count": 90, "total_files": 96}
        items = [
            {
                "id": table_id,
                "schema_name": "fixtures",
                "table_name": f"fixture_{index}",
                "stats": stats if self.graded else None,
            }
            for index, table_id in enumerate(TABLE_IDS)
        ]
        return 200, {"items": items, "total": len(items)}

    def transport(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        parsed = urllib.parse.urlsplit(url)
        call = {
            "method": method,
            "path": parsed.path,
            "query": dict(urllib.parse.parse_qsl(parsed.query)),
            "headers": dict(headers),
            "body": json.loads(body) if body else None,
        }
        self.calls.append(call)
        run_match = re.fullmatch(r"/api/v1/runs/([^/]+)", parsed.path)
        if method == "GET" and run_match:
            statuses = self.run_statuses.setdefault(run_match.group(1), ["running", "succeeded"])
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            return 200, json.dumps({"id": run_match.group(1), "status": status}).encode()
        handler = self.handlers.get((method, parsed.path))
        if handler is None:
            return 404, b'{"detail": "not found"}'
        status, payload = handler(call)
        return status, json.dumps(payload).encode()

    def bodies(self, method: str, path: str) -> list[Any]:
        return [c["body"] for c in self.calls if c["method"] == method and c["path"] == path]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _setup(api: FakeApi) -> tuple[demo.Client, demo.DemoConfig, list[str]]:
    lines: list[str] = []
    clock = FakeClock()
    cfg = demo.DemoConfig(
        clock=demo.Clock(sleep=clock.sleep, monotonic=clock.monotonic), out=lines.append
    )
    return demo.Client("http://api.test", transport=api.transport), cfg, lines


def test_full_demo_scans_assigns_runs_and_prints_the_run_url() -> None:
    api = FakeApi()
    client, cfg, lines = _setup(api)

    assert demo.run_demo(client, cfg, "all") == 0

    expected_filter = {
        "all": [
            {"field": "source_id", "op": "in", "value": [SOURCE_ID]},
            {"field": "schema_name", "op": "=", "value": "fixtures"},
        ]
    }
    scans = api.bodies("POST", "/api/v1/scans")
    assert scans[0] == {"scope": "source", "source_id": SOURCE_ID, "trigger": "make-demo"}
    assert scans[1] == {"scope": "tables", "table_ids": TABLE_IDS, "trigger": "make-demo"}
    assert api.bodies("POST", "/api/v1/assignments") == [
        {
            "selection": {"mode": "filter", "filter": expected_filter},
            "preset_id": PRESET_ID,
            "assigned_by": "make-demo",
        }
    ]
    assert api.bodies("POST", "/api/v1/maintenance/run") == [
        {
            "selection": {"mode": "filter", "filter": expected_filter},
            "destination_id": DESTINATION_ID,
            "preset_id": PRESET_ID,
            "mode": "deep",
            "operations": ["optimize", "optimize_manifests"],
            "trigger": "make-demo",
        }
    ]
    token_call = next(c for c in api.calls if c["path"] == "/api/v1/auth/token")
    assert token_call["body"]["role"] == "operator"
    authed = [c for c in api.calls if c["path"] not in {"/healthz", "/api/v1/auth/token"}]
    assert all(c["headers"]["Authorization"] == "Bearer tok" for c in authed)
    output = "\n".join(lines)
    assert "grade D" in output
    assert "http://127.0.0.1:5173/runs/run-1" in output


def test_every_request_matches_the_real_api_contract() -> None:
    """The fake API accepts anything, so check the recorded calls against the real routes and
    request schemas the server would validate them with."""
    from cryo.api.app import create_app
    from cryo.api.auth_routes import TokenRequest
    from cryo.api.predicates import compile_predicate
    from cryo.api.schemas import AssignmentRequest, MaintenanceRunRequest, ScanTriggerRequest

    api = FakeApi()
    client, cfg, _ = _setup(api)
    demo.run_demo(client, cfg, "all")

    paths = create_app().openapi()["paths"]

    def served(method: str, path: str) -> bool:
        return any(
            method.lower() in operations
            and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for template, operations in paths.items()
        )

    for call in api.calls:
        assert served(call["method"], call["path"]), (call["method"], call["path"])
    schemas = {
        "/api/v1/auth/token": TokenRequest,
        "/api/v1/scans": ScanTriggerRequest,
        "/api/v1/assignments": AssignmentRequest,
        "/api/v1/maintenance/run": MaintenanceRunRequest,
    }
    for call in api.calls:
        if call["method"] == "POST":
            schemas[call["path"]].model_validate(call["body"], strict=False)
    table_list = next(c for c in api.calls if c["path"] == "/api/v1/tables")
    assert compile_predicate(json.loads(table_list["query"]["filter"])) is not None


def test_demo_operations_finish_without_an_approval() -> None:
    """The run must reach a terminal state on its own. Any op that waits for approval would
    leave it deferred and the demo would time out."""
    assert set(demo.DEMO_OPERATIONS) <= set(LOCAL_STANDARD_PRESET["operations"])
    assert not any(requires_approval(OperationType(op)) for op in demo.DEMO_OPERATIONS)


def test_scan_already_active_waits_for_it_then_retries() -> None:
    api = FakeApi()
    attempts = {"n": 0}

    def scan(call: dict[str, Any]) -> tuple[int, Any]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 400, {"detail": "A scan is already active for this source"}
        api.graded = True
        return 202, {"run_id": "scan-new", "queued": True, "message": "queued"}

    api.on("POST", "/api/v1/scans", scan)
    api.on("GET", "/api/v1/runs", lambda _: (200, [{"id": "scan-old", "status": "running"}]))
    client, cfg, _ = _setup(api)

    demo.run_demo(client, cfg, "scan")

    polled = [c["path"] for c in api.calls if c["path"].startswith("/api/v1/runs/")]
    assert polled.index("/api/v1/runs/scan-old") < polled.index("/api/v1/runs/scan-new")
    assert attempts["n"] == 2


def test_run_already_covered_follows_the_earlier_demo_run() -> None:
    api = FakeApi()
    api.on(
        "POST",
        "/api/v1/maintenance/run",
        lambda _: (409, {"detail": "already covered"}),
    )
    api.on(
        "GET",
        "/api/v1/runs",
        lambda call: (
            200,
            [
                {"id": "other", "status": "running", "trigger": "api"},
                {"id": "earlier-demo", "status": "running", "trigger": "make-demo"},
            ],
        ),
    )
    api.graded = True
    client, cfg, lines = _setup(api)

    demo.run_demo(client, cfg, "run")

    assert any(c["path"] == "/api/v1/runs/earlier-demo" for c in api.calls)
    assert "http://127.0.0.1:5173/runs/earlier-demo" in "\n".join(lines)


def test_failed_run_points_at_its_page() -> None:
    api = FakeApi()
    api.run_statuses["run-1"] = ["running", "failed"]
    api.graded = True
    client, cfg, _ = _setup(api)

    with pytest.raises(demo.DemoError, match=r"ended 'failed'.*5173/runs/run-1"):
        demo.run_demo(client, cfg, "run")


def test_run_that_never_finishes_times_out_with_a_hint() -> None:
    api = FakeApi()
    api.run_statuses["run-1"] = ["queued"]
    client, cfg, _ = _setup(api)
    cfg.run_timeout = 30

    with pytest.raises(demo.DemoError, match=r"still 'queued' after 30s.*start-local.sh logs"):
        demo.step_run(client, cfg)


def test_missing_source_says_how_to_register_it() -> None:
    api = FakeApi()
    api.on("GET", "/api/v1/sources", lambda _: (200, []))
    client, cfg, _ = _setup(api)

    with pytest.raises(demo.DemoError, match="make seed-config"):
        demo.run_demo(client, cfg, "all")


def test_scan_with_no_fixture_tables_says_to_seed() -> None:
    api = FakeApi()
    api.on("GET", "/api/v1/tables", lambda _: (200, {"items": [], "total": 0}))
    client, cfg, _ = _setup(api)

    with pytest.raises(demo.DemoError, match="make seed-fragmented"):
        demo.run_demo(client, cfg, "scan")


def test_dev_sign_in_disabled_explains_the_local_only_scope() -> None:
    api = FakeApi()
    api.on("POST", "/api/v1/auth/token", lambda _: (503, {"detail": "Token issuance is disabled"}))
    client, cfg, _ = _setup(api)

    with pytest.raises(demo.DemoError, match="CRYO_ENVIRONMENT=dev"):
        demo.run_demo(client, cfg, "all")


def test_api_down_gives_up_with_start_instructions() -> None:
    def refuse(*_: object) -> tuple[int, bytes]:
        raise ConnectionRefusedError("refused")

    clock = FakeClock()
    cfg = demo.DemoConfig(clock=demo.Clock(sleep=clock.sleep, monotonic=clock.monotonic))
    with pytest.raises(demo.DemoError, match="start-local.sh start"):
        demo.wait_for_api(demo.Client("http://api.test", transport=refuse), cfg, timeout=10)


def test_main_reports_a_dropped_connection_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo, "wait_for_api", lambda *_args, **_kwargs: None)

    assert demo.main(["scan", "--api-url", "http://127.0.0.1:9"]) == 1
    assert "error: lost the Cryo API at http://127.0.0.1:9" in capsys.readouterr().err


def test_fragmented_seed_targets_the_rest_catalog_from_inside_the_container() -> None:
    """scripts/spark_session.py only speaks REST and defaults to localhost:8181, which inside
    the spark container is the container itself. The Makefile has to point it at the service."""
    makefile = (ROOT / "Makefile").read_text()
    runner = re.search(r"^SPARK_REST_RUNNER = (.+?)\n(?=\S)", makefile, re.M | re.S)
    assert runner is not None
    assert "ICEBERG_CATALOG=rest" in runner.group(1)
    assert "ICEBERG_REST_URI=http://iceberg-rest:8181" in runner.group(1)
    for target in ("seed-fragmented", "seed-docker"):
        recipe = re.search(rf"^{target}:.*\n\t(.+)", makefile, re.M)
        assert recipe is not None
        assert recipe.group(1).startswith("$(SPARK_REST_RUNNER)")
    demo_recipe = re.search(r"^demo:\n((?:\t.+\n)+)", makefile, re.M)
    assert demo_recipe is not None
    assert "seed-fragmented" in demo_recipe.group(1)
    assert "scripts/demo.py" in demo_recipe.group(1)
