#!/usr/bin/env python3
"""Take the local stack from an empty UI to graded tables and one finished maintenance run.

Talks only to the Cryo API, the same path the UI uses. Every step is safe to rerun:
lookups are by name, a scan or run that is already in flight is waited on rather than
duplicated, and assigning the same preset twice changes nothing.

Usage:
    python scripts/demo.py [all|scan|assign|run] [--api-url URL] [--web-url URL]

`make demo` starts the stack, seeds the fragmented fixture tables and then runs `all`.
Standard library only, so it runs on the host Python without the project venv.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_WEB_URL = "http://127.0.0.1:5173"
DEFAULT_SOURCE = "local-rest"
DEFAULT_SCHEMA = "fixtures"
DEFAULT_PRESET = "local-standard"
TRIGGER = "make-demo"
# expire_snapshots and remove_orphan_files wait for a human approval by default, so a run
# that included them would sit deferred instead of finishing.
DEMO_OPERATIONS = ("optimize", "optimize_manifests")
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "partial", "partial_failed", "cancelled", "skipped"}
)

Transport = Callable[[str, str, Mapping[str, str], "bytes | None"], tuple[int, bytes]]


class DemoError(RuntimeError):
    """A step failed in a way the reader can act on; the message says how."""


class ApiError(DemoError):
    def __init__(self, method: str, path: str, status: int, detail: object) -> None:
        super().__init__(f"{method} {path} returned HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - local URL
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@dataclass
class Client:
    api_url: str
    transport: Transport = urllib_transport
    token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        query: Mapping[str, str] | None = None,
    ) -> Any:
        url = self.api_url.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        status, raw = self.transport(method, url, headers, data)
        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw.decode(errors="replace")
        if status >= 400:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise ApiError(method, path, status, detail)
        return payload


@dataclass
class Clock:
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass
class DemoConfig:
    web_url: str = DEFAULT_WEB_URL
    source: str = DEFAULT_SOURCE
    schema: str = DEFAULT_SCHEMA
    preset: str = DEFAULT_PRESET
    scan_timeout: float = 600
    run_timeout: float = 900
    poll_interval: float = 5
    clock: Clock = field(default_factory=Clock)
    out: Callable[[str], None] = print


def wait_for_api(client: Client, cfg: DemoConfig, timeout: float = 120) -> None:
    deadline = cfg.clock.monotonic() + timeout
    while True:
        try:
            client.request("GET", "/healthz")
            return
        except (OSError, DemoError):
            if cfg.clock.monotonic() >= deadline:
                raise DemoError(
                    f"The Cryo API at {client.api_url} is not answering. "
                    "Start the stack with ./start-local.sh start, then rerun."
                ) from None
            cfg.clock.sleep(2)


def sign_in(client: Client) -> None:
    try:
        response = client.request(
            "POST", "/api/v1/auth/token", body={"role": "operator", "subject": TRIGGER}
        )
    except ApiError as exc:
        raise DemoError(
            "Dev sign-in is off, so this script cannot get a token. It only works against "
            f"the local stack (CRYO_ENVIRONMENT=dev, no OIDC issuer). {exc}"
        ) from exc
    client.token = str(response["access_token"])


def find_by_name(items: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    return next((item for item in items if item.get("name") == name), None)


def find_source(client: Client, cfg: DemoConfig) -> Mapping[str, Any]:
    source = find_by_name(client.request("GET", "/api/v1/sources"), cfg.source)
    if source is None:
        raise DemoError(
            f"Source {cfg.source!r} is not registered. ./start-local.sh start registers it; "
            "if you started with SEED_CONFIG=0, run `make seed-config`."
        )
    return source


def find_preset(client: Client, cfg: DemoConfig) -> Mapping[str, Any]:
    preset = find_by_name(client.request("GET", "/api/v1/presets"), cfg.preset)
    if preset is None:
        raise DemoError(
            f"Preset {cfg.preset!r} does not exist. Run `make seed-config` to create the "
            "local presets."
        )
    if preset.get("paused"):
        raise DemoError(f"Preset {cfg.preset!r} is paused. Resume it on the Runs page first.")
    return preset


def table_filter(source_id: str, schema: str) -> dict[str, Any]:
    return {
        "all": [
            {"field": "source_id", "op": "in", "value": [source_id]},
            {"field": "schema_name", "op": "=", "value": schema},
        ]
    }


def list_tables(client: Client, source_id: str, schema: str) -> list[Mapping[str, Any]]:
    page = client.request(
        "GET",
        "/api/v1/tables",
        query={"filter": json.dumps(table_filter(source_id, schema)), "limit": "100"},
    )
    return list(page.get("items") or [])


def wait_for_run(
    client: Client, cfg: DemoConfig, run_id: str, *, label: str, timeout: float
) -> str:
    deadline = cfg.clock.monotonic() + timeout
    last_status: str | None = None
    while True:
        run = client.request("GET", f"/api/v1/runs/{run_id}")
        status = str(run.get("status") or "")
        if status != last_status:
            cfg.out(f"    {label} {run_id}: {status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return status
        if cfg.clock.monotonic() >= deadline:
            raise DemoError(
                f"{label} {run_id} is still {status!r} after {timeout:.0f}s. "
                f"Open {cfg.web_url}/runs/{run_id} to see what it is waiting on, "
                "and ./start-local.sh logs for the worker and governor."
            )
        cfg.clock.sleep(cfg.poll_interval)


def _active_runs(client: Client, query: Mapping[str, str]) -> list[Mapping[str, Any]]:
    runs = client.request("GET", "/api/v1/runs", query={**query, "limit": "50"})
    return [run for run in runs if str(run.get("status") or "") not in TERMINAL_STATUSES]


def _trigger_scan(client: Client, body: Mapping[str, Any]) -> str | None:
    try:
        response = client.request("POST", "/api/v1/scans", body=body)
    except ApiError as exc:
        if exc.status == 400 and "already active" in str(exc.detail):
            return None
        raise
    if not response.get("queued"):
        raise DemoError(
            f"The scan was accepted but not queued: {response.get('message')} "
            "Check that Redis is up (./start-local.sh status)."
        )
    return str(response["run_id"])


def scan(client: Client, cfg: DemoConfig, body: Mapping[str, Any]) -> None:
    run_id = _trigger_scan(client, body)
    if run_id is None:
        cfg.out("    A scan is already running; waiting for it before starting this one.")
        for run in _active_runs(client, {"kind": "scan"}):
            wait_for_run(client, cfg, str(run["id"]), label="scan", timeout=cfg.scan_timeout)
        run_id = _trigger_scan(client, body)
        if run_id is None:
            raise DemoError("Another scan is still active. Wait for it to finish, then rerun.")
    status = wait_for_run(client, cfg, run_id, label="scan", timeout=cfg.scan_timeout)
    if status != "succeeded":
        raise DemoError(
            f"Scan {run_id} ended {status!r}. Open {cfg.web_url}/runs/{run_id} for the error."
        )


def print_grades(cfg: DemoConfig, tables: Sequence[Mapping[str, Any]]) -> None:
    for table in sorted(tables, key=lambda item: str(item.get("table_name"))):
        stats = table.get("stats") or {}
        name = f"{table.get('schema_name')}.{table.get('table_name')}"
        if not stats:
            cfg.out(f"    {name:<36} not graded yet")
            continue
        cfg.out(
            f"    {name:<36} grade {stats.get('grade')}  score {stats.get('overall_score'):>3}  "
            f"{stats.get('small_files_count')}/{stats.get('total_files')} files small"
        )


def step_scan(client: Client, cfg: DemoConfig) -> list[Mapping[str, Any]]:
    source = find_source(client, cfg)
    source_id = str(source["id"])
    cfg.out(f"==> Scanning source {cfg.source}")
    scan(client, cfg, {"scope": "source", "source_id": source_id, "trigger": TRIGGER})
    tables = list_tables(client, source_id, cfg.schema)
    graded = [table for table in tables if table.get("stats")]
    if not graded:
        raise DemoError(
            f"The scan found no graded tables in {cfg.source} schema {cfg.schema!r}. "
            "Seed them with `make seed-fragmented`, then rerun."
        )
    print_grades(cfg, tables)
    return graded


def step_assign(client: Client, cfg: DemoConfig) -> Mapping[str, Any]:
    source = find_source(client, cfg)
    preset = find_preset(client, cfg)
    cfg.out(f"==> Assigning preset {cfg.preset} to {cfg.source} schema {cfg.schema}")
    response = client.request(
        "POST",
        "/api/v1/assignments",
        body={
            "selection": {"mode": "filter", "filter": table_filter(str(source["id"]), cfg.schema)},
            "preset_id": str(preset["id"]),
            "assigned_by": TRIGGER,
        },
    )
    affected = int(response.get("affected") or 0)
    if affected == 0:
        raise DemoError(
            f"No tables in {cfg.source} schema {cfg.schema!r} to assign. Run the scan step first."
        )
    cfg.out(f"    {affected} tables now use {cfg.preset}")
    return preset


def _start_run(client: Client, body: Mapping[str, Any], preset_id: str) -> str:
    try:
        return str(client.request("POST", "/api/v1/maintenance/run", body=body)["run_id"])
    except ApiError as exc:
        if exc.status != 409:
            raise
        # Every table already has these operations queued, most likely from an earlier
        # `make demo` that was interrupted. Follow that run instead of forcing a second one.
        for run in _active_runs(client, {"preset_id": preset_id}):
            if run.get("trigger") == TRIGGER:
                return str(run["id"])
        raise


def step_run(client: Client, cfg: DemoConfig) -> str:
    source = find_source(client, cfg)
    preset = find_preset(client, cfg)
    source_id = str(source["id"])
    preset_id = str(preset["id"])
    cfg.out(f"==> Running {', '.join(DEMO_OPERATIONS)} with preset {cfg.preset}")
    run_id = _start_run(
        client,
        {
            "selection": {"mode": "filter", "filter": table_filter(source_id, cfg.schema)},
            "destination_id": str(preset["destination_id"]),
            "preset_id": preset_id,
            "mode": preset.get("mode") or "standard",
            "operations": list(DEMO_OPERATIONS),
            "trigger": TRIGGER,
        },
        preset_id,
    )
    status = wait_for_run(client, cfg, run_id, label="run", timeout=cfg.run_timeout)
    if status != "succeeded":
        raise DemoError(
            f"Maintenance run {run_id} ended {status!r}. "
            f"Open {cfg.web_url}/runs/{run_id} to see which job failed and why."
        )
    tables = list_tables(client, source_id, cfg.schema)
    table_ids = [str(table["id"]) for table in tables]
    if table_ids:
        cfg.out("==> Rescanning the maintained tables")
        scan(client, cfg, {"scope": "tables", "table_ids": table_ids, "trigger": TRIGGER})
        print_grades(cfg, list_tables(client, source_id, cfg.schema))
    return run_id


def run_demo(client: Client, cfg: DemoConfig, step: str) -> int:
    started = cfg.clock.monotonic()
    wait_for_api(client, cfg)
    sign_in(client)
    run_id: str | None = None
    if step in {"all", "scan"}:
        step_scan(client, cfg)
    if step in {"all", "assign"}:
        step_assign(client, cfg)
    if step in {"all", "run"}:
        run_id = step_run(client, cfg)
    elapsed = cfg.clock.monotonic() - started
    cfg.out("")
    cfg.out(f"Done in {elapsed / 60:.1f} min. Open:")
    cfg.out(f"  Home      {cfg.web_url}/")
    cfg.out(f"  Tables    {cfg.web_url}/tables")
    if run_id is not None:
        cfg.out(f"  This run  {cfg.web_url}/runs/{run_id}")
    else:
        cfg.out(f"  Runs      {cfg.web_url}/runs")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "step",
        nargs="?",
        default="all",
        choices=["all", "scan", "assign", "run"],
        help="Which step to run (default: all three, in order)",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--web-url", default=DEFAULT_WEB_URL)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--preset", default=DEFAULT_PRESET)
    parser.add_argument("--scan-timeout", type=float, default=600, help="Seconds (default 600)")
    parser.add_argument("--run-timeout", type=float, default=900, help="Seconds (default 900)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = DemoConfig(
        web_url=args.web_url.rstrip("/"),
        source=args.source,
        schema=args.schema,
        preset=args.preset,
        scan_timeout=args.scan_timeout,
        run_timeout=args.run_timeout,
    )
    try:
        return run_demo(Client(args.api_url), cfg, args.step)
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: lost the Cryo API at {args.api_url}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
