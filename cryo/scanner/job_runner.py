"""One-shot Job entrypoint for a single dispatched scan chunk.

Mirrors cryo.engines.native.batch's CLI shape (decode CRYO_PAYLOAD_B64, run,
print one JSON line, exit) but for a scan chunk instead of a maintenance
Job. Reuses ScanWorker.process_next() unchanged -- the payload already
carries ``_already_claimed: True`` (the Governor claimed it from the
ScanQueue/DB-stranded fallback before dispatching this Job), so this just
runs the scan and records the outcome exactly as the always-on scanner-worker
Deployment does today.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from typing import Any

from cryo.queue.scan_queue import InMemoryScanQueue
from cryo.scanner.worker import ScanWorker

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    del argv
    raw_b64 = os.environ.get("CRYO_PAYLOAD_B64")
    if not raw_b64:
        print(json.dumps({"ok": False, "error": "CRYO_PAYLOAD_B64 not set"}), flush=True)
        return 1
    try:
        payload: dict[str, Any] = json.loads(base64.b64decode(raw_b64).decode())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid payload: {exc}"}), flush=True)
        return 1

    queue = InMemoryScanQueue()
    queue.enqueue(str(payload.get("run_id") or ""), {**payload, "_already_claimed": True})
    worker = ScanWorker(queue=queue)

    try:
        result = worker.process_next()
    except Exception as exc:
        logger.exception("Scan chunk job failed")
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 1

    status = result.status if result else "missing"
    ok = status in {"succeeded", "skipped", "cancelled"}
    print(
        json.dumps(
            {
                "ok": ok,
                "run_id": result.run_id if result else payload.get("run_id"),
                "status": status,
            }
        ),
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
