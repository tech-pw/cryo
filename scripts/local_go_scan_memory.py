#!/usr/bin/env python3
"""Run a Cryo CDP (dev-hms) catalog scan with Go engine and sample RSS memory.

Usage:
  .venv/bin/python scripts/local_go_scan_memory.py
  .venv/bin/python scripts/local_go_scan_memory.py --schema cdp_revenue --parallel 2
  .venv/bin/python scripts/local_go_scan_memory.py --full --parallel 2
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / ".local" / "scan-memory"
BINARY = REPO / "engine-go" / "bin" / "cryo-engine-go"


def _rss_mb(pid: int) -> float | None:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)
        return int(out.strip()) / 1024.0
    except (subprocess.CalledProcessError, ValueError, PermissionError, OSError):
        return None


def _child_pids(root: int) -> list[int]:
    try:
        out = subprocess.check_output(["pgrep", "-P", str(root)], text=True)
    except (subprocess.CalledProcessError, PermissionError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid = int(line)
        pids.append(pid)
        pids.extend(_child_pids(pid))
    return pids


def _self_maxrss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS ru_maxrss is bytes; Linux is KiB.
    if sys.platform == "darwin":
        return ru / (1024 * 1024)
    return ru / 1024


def _resolve_thrift_uri(uri: str) -> str:
    """Replace thrift hostname with an A-record IP so scans work when corporate DNS is flaky."""
    from urllib.parse import urlparse, urlunparse
    import socket

    parsed = urlparse(uri)
    if parsed.scheme != "thrift" or not parsed.hostname:
        return uri
    try:
        ip = socket.getaddrinfo(parsed.hostname, parsed.port or 9083, type=socket.SOCK_STREAM)[0][4][
            0
        ]
    except OSError as exc:
        print(f"DNS resolve failed for {parsed.hostname}: {exc}", file=sys.stderr)
        return uri
    netloc = f"{ip}:{parsed.port}" if parsed.port else ip
    resolved = urlunparse(parsed._replace(netloc=netloc))
    print(f"resolved catalog uri {parsed.hostname} -> {ip}", flush=True)
    return resolved


def _sample_tree(root: int) -> dict[str, float]:
    pids = [root, *_child_pids(root)]
    by_pid: dict[str, float] = {}
    total = 0.0
    for pid in pids:
        rss = _rss_mb(pid)
        if rss is None:
            continue
        by_pid[str(pid)] = round(rss, 1)
        total += rss
    by_pid["total"] = round(total, 1)
    return by_pid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="dev-hms", help="Cryo source name (CDP = dev-hms)")
    parser.add_argument("--schema", action="append", default=[], help="Limit to schema(s)")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--full", action="store_true", help="Ignore already-scanned-today skip")
    parser.add_argument("--interval", type=float, default=1.0, help="RSS sample interval seconds")
    parser.add_argument(
        "--cost-tier",
        default="heuristic",
        choices=("heuristic", "metadata", "storage_truth"),
        help="Cost tier (heuristic isolates scanner memory from Tier-1)",
    )
    args = parser.parse_args()

    if not BINARY.is_file():
        print(f"missing go binary: {BINARY}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    samples_path = OUT_DIR / f"go-scan-{args.source}-{stamp}.samples.jsonl"
    summary_path = OUT_DIR / f"go-scan-{args.source}-{stamp}.summary.json"

    os.environ["CRYO_SCAN_ENGINE"] = "go"
    os.environ["CRYO_SCAN_GO_BINARY"] = str(BINARY)
    os.environ["CRYO_COST_SCAN_TIER"] = args.cost_tier
    os.environ.setdefault(
        "CRYO_DATABASE_URL",
        "postgresql+psycopg://cryo:cryo@127.0.0.1:5432/cryo",
    )

    # Import after env so Settings picks up scan_engine.
    sys.path.insert(0, str(REPO))
    from cryo.db.models import Source
    from cryo.db.session import SessionLocal
    from cryo.scanner.run import run_scan
    from sqlalchemy import select

    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.name == args.source))
        if source is None:
            print(f"source not found: {args.source}", file=sys.stderr)
            return 2
        source_id = UUID(str(source.id))
        # Ephemeral URI rewrite for this process only (do not persist secrets/hostname changes).
        cfg = dict(source.config or {})
        if isinstance(cfg.get("uri"), str):
            cfg["uri"] = _resolve_thrift_uri(str(cfg["uri"]))
            source.config = cfg
        print(
            f"scanning source={args.source} id={source_id} "
            f"schemas={args.schema or 'ALL'} parallel={args.parallel} engine=go",
            flush=True,
        )

        stop = threading.Event()
        samples: list[dict] = []
        peak_total = 0.0
        peak_self = 0.0
        root_pid = os.getpid()

        def sampler() -> None:
            nonlocal peak_total, peak_self
            while not stop.wait(args.interval):
                tree = _sample_tree(root_pid)
                self_mb = _self_maxrss_mb()
                row = {
                    "ts": datetime.now(UTC).isoformat(),
                    "self_maxrss_mb": round(self_mb, 1),
                    "tree_rss_mb": tree,
                }
                samples.append(row)
                peak_total = max(peak_total, float(tree.get("total") or 0), self_mb)
                peak_self = max(peak_self, self_mb)
                with samples_path.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(
                    f"[mem] self_maxrss={self_mb:.1f}MB tree_total={tree.get('total')}MB",
                    flush=True,
                )

        t0 = time.perf_counter()
        sampler_thread = threading.Thread(target=sampler, name="rss-sampler", daemon=True)
        sampler_thread.start()
        error: str | None = None
        summary_obj = None
        try:
            summary_obj = run_scan(
                session,
                source_id=source_id,
                full_scan=args.full,
                parallel_tables=args.parallel,
                schema_names=args.schema or None,
            )
            print(f"scan finished: {summary_obj}", flush=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"scan failed: {error}", file=sys.stderr, flush=True)
        finally:
            stop.set()
            sampler_thread.join(timeout=2)
        elapsed = time.perf_counter() - t0

        final_tree = _sample_tree(root_pid)
        peak_self = max(peak_self, _self_maxrss_mb())
        peak_total = max(peak_total, float(final_tree.get("total") or 0), peak_self)

        payload = {
            "source": args.source,
            "schemas": args.schema or ["*"],
            "parallel": args.parallel,
            "cost_tier": args.cost_tier,
            "scan_engine": "go",
            "binary": str(BINARY),
            "elapsed_sec": round(elapsed, 2),
            "peak_tree_rss_mb": round(peak_total, 1),
            "peak_self_maxrss_mb": round(peak_self, 1),
            "final_tree_rss_mb": final_tree,
            "sample_count": len(samples),
            "samples_path": str(samples_path),
            "summary": None
            if summary_obj is None
            else {
                "discovered": summary_obj.discovered,
                "assigned": summary_obj.assigned,
                "scanned": summary_obj.scanned,
                "skipped": summary_obj.skipped,
                "failed": summary_obj.failed,
                "cancelled": summary_obj.cancelled,
            },
            "error": error,
            "started_at": stamp,
        }
        summary_path.write_text(json.dumps(payload, indent=2))
        print(json.dumps(payload, indent=2), flush=True)
        print(f"wrote {summary_path}", flush=True)
        return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
