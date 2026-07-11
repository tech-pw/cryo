#!/usr/bin/env bash
# Monitor RSS for Cryo scanner / cryo-engine-go while a scan run is active.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/.local/scan-memory"
mkdir -p "$OUT"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SAMPLES="$OUT/live-$STAMP.jsonl"
SUMMARY="$OUT/live-$STAMP.summary.json"
INTERVAL="${INTERVAL:-2}"
DURATION_SEC="${DURATION_SEC:-600}"
RUN_ID="${RUN_ID:-e7ea5c24-dc19-4d52-86b8-ec0054c9fdca}"

echo "sampling every ${INTERVAL}s for up to ${DURATION_SEC}s -> $SAMPLES" >&2

python3 - "$SAMPLES" "$SUMMARY" "$INTERVAL" "$DURATION_SEC" "$RUN_ID" <<'PY'
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
import sys

samples_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
interval = float(sys.argv[3])
duration = float(sys.argv[4])
run_id = sys.argv[5]


def run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError, OSError):
        return ""


def listen_pids(port: int) -> list[int]:
    out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def pgrep_f(pattern: str) -> list[int]:
    out = run(["pgrep", "-f", pattern])
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def children(pid: int) -> list[int]:
    out = run(["pgrep", "-P", str(pid)])
    kids = [int(x) for x in out.split() if x.strip().isdigit()]
    all_kids = list(kids)
    for k in kids:
        all_kids.extend(children(k))
    return all_kids


def rss_mb(pid: int) -> float | None:
    out = run(["ps", "-o", "rss=", "-p", str(pid)]).strip()
    if not out:
        return None
    try:
        return int(out) / 1024.0
    except ValueError:
        return None


def cmdline(pid: int) -> str:
    return run(["ps", "-o", "command=", "-p", str(pid)]).strip()


def http_json(method: str, url: str, body: dict | None = None, token: str | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def run_progress(run_id: str) -> dict:
    try:
        tok = http_json(
            "POST",
            "http://127.0.0.1:8000/api/v1/auth/token",
            {"role": "admin", "sub": "monitor"},
        )["access_token"]
        d = http_json("GET", f"http://127.0.0.1:8000/api/v1/runs/{run_id}", token=tok)
        s = d.get("summary") or {}
        chunk0 = (s.get("chunks") or {}).get("0") or {}
        return {
            "status": d.get("status"),
            "scanned": s.get("scanned"),
            "discovered": s.get("discovered"),
            "failed": s.get("failed"),
            "chunks_running": s.get("chunks_running"),
            "chunks_done": s.get("chunks_done"),
            "chunks_pending": s.get("chunks_pending"),
            "heartbeat_at": chunk0.get("heartbeat_at"),
            "current_table": s.get("current_table") or chunk0.get("current_table"),
        }
    except Exception as exc:
        return {"error": str(exc)}


def sample_once() -> dict:
    roots = listen_pids(8084)
    go_roots = pgrep_f("cryo-engine-go|scan-stats")
    pids = set(roots)
    for r in roots:
        pids.update(children(r))
    pids.update(go_roots)

    procs: dict[str, float] = {}
    py_mb = 0.0
    go_mb = 0.0
    for pid in sorted(pids):
        mb = rss_mb(pid)
        if mb is None:
            continue
        cmd = cmdline(pid)
        kind = "go" if ("cryo-engine-go" in cmd or "scan-stats" in cmd) else "python"
        procs[f"{pid}:{kind}"] = round(mb, 1)
        if kind == "go":
            go_mb += mb
        else:
            py_mb += mb
    return {
        "ts": datetime.now(UTC).isoformat(),
        "total_rss_mb": round(py_mb + go_mb, 1),
        "python_rss_mb": round(py_mb, 1),
        "go_rss_mb": round(go_mb, 1),
        "procs": procs,
        "run": run_progress(run_id),
    }


peak_total = peak_py = peak_go = 0.0
n = 0
deadline = time.time() + duration
with samples_path.open("w") as fh:
    while time.time() < deadline:
        row = sample_once()
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        n += 1
        peak_total = max(peak_total, float(row["total_rss_mb"]))
        peak_py = max(peak_py, float(row["python_rss_mb"]))
        peak_go = max(peak_go, float(row["go_rss_mb"]))
        print(
            f"[mem] total={row['total_rss_mb']}MB py={row['python_rss_mb']}MB "
            f"go={row['go_rss_mb']}MB run={row['run']}",
            flush=True,
        )
        st = (row.get("run") or {}).get("status")
        if st in {"succeeded", "failed", "cancelled"}:
            print(f"run finished: {st}", flush=True)
            break
        time.sleep(interval)

summary = {
    "samples": n,
    "peak_total_rss_mb": round(peak_total, 1),
    "peak_python_rss_mb": round(peak_py, 1),
    "peak_go_rss_mb": round(peak_go, 1),
    "samples_path": str(samples_path),
    "run_id": run_id,
}
summary_path.write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2), flush=True)
print(f"wrote {summary_path}", flush=True)
PY
