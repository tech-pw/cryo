from __future__ import annotations

import pytest

from cryo.queue.scan_queue import InMemoryScanQueue
from cryo.scheduler.cron import validate_cron_expression


def test_validate_cron_expression_rejects_invalid_ranges() -> None:
    assert validate_cron_expression("0 2 * * *")
    assert not validate_cron_expression("0 25 * * *")
    with pytest.raises(ValueError):
        validate_cron_expression("0 2", raise_error=True)


@pytest.mark.parametrize(
    "expression",
    [
        "0 2 * * *; DROP TABLE cryo.tables",
        "0 2 * * *\n* * * * *",
        "0 2 * * *\x00",
        "0 */0 * * *",
        "0 */ * * *",
        "0 2 * * *" + (" " * 101),
    ],
)
def test_validate_cron_expression_rejects_malicious_or_unsafe_input(expression: str) -> None:
    assert not validate_cron_expression(expression)


def test_scan_queue_enqueue_chunks_adds_chunk_metadata() -> None:
    queue = InMemoryScanQueue()

    queue.enqueue_chunks(
        "run-1",
        {"scope": "all", "full_scan": False, "cost_full_scan": False},
        num_chunks=3,
    )

    assert queue.pending_count() == 3
    first = queue.claim()
    second = queue.claim()
    third = queue.claim()
    assert first is not None and first["chunk_id"] == 0 and first["num_chunks"] == 3
    assert second is not None and second["chunk_id"] == 1 and second["num_chunks"] == 3
    assert third is not None and third["chunk_id"] == 2 and third["num_chunks"] == 3
