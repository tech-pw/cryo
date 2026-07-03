from __future__ import annotations

from cryo.planner.chunking import ChunkedPartition, greedy_pack


def test_greedy_pack_respects_budget_part_cap_oversize_and_chunk_limit() -> None:
    candidates = [
        ChunkedPartition("a", size_gb=6, small_files=60),
        ChunkedPartition("b", size_gb=4, small_files=40),
        ChunkedPartition("c", size_gb=3, small_files=30),
        ChunkedPartition("d", size_gb=12, small_files=120),
        ChunkedPartition("e", size_gb=1, small_files=10),
    ]

    chunks = greedy_pack(candidates, gb_budget=10, max_parts=2, max_chunks=3)

    assert [[item.value for item in chunk] for chunk in chunks] == [["a", "b"], ["c"], ["d"]]
