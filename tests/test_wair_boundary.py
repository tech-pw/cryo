from cryo.optimizer.wair import (
    BoundaryFile,
    FileBounds,
    QueryRange,
    find_boundary_files,
    select_boundary_files,
)


def test_boundary_detection_includes_edges_and_excludes_interior_files() -> None:
    files = [
        FileBounds("lower.parquet", 100, {"value": 0}, {"value": 12}),
        FileBounds("interior.parquet", 100, {"value": 12}, {"value": 18}),
        FileBounds("upper.parquet", 100, {"value": 18}, {"value": 25}),
        FileBounds("outside.parquet", 100, {"value": 30}, {"value": 40}),
    ]

    result = find_boundary_files(files, [QueryRange("value", 10, 20, frequency=3)])

    assert [item.file_path for item in result] == ["lower.parquet", "upper.parquet"]
    assert all(item.utilization_score == 3 for item in result)


def test_boundary_selection_respects_byte_and_file_budgets() -> None:
    boundaries = [
        BoundaryFile("hot.parquet", 200, 10, 2),
        BoundaryFile("warm.parquet", 100, 5, 1),
        BoundaryFile("cold.parquet", 100, 1, 1),
        BoundaryFile("oversize.parquet", 1_000, 50, 2),
    ]

    result = select_boundary_files(boundaries, rewrite_byte_budget=300, max_files=2)

    assert [item.file_path for item in result.selected] == ["hot.parquet", "warm.parquet"]
    assert result.selected_bytes == 300
    assert result.skipped_oversize == 1
    assert result.truncated is True


def test_boundary_detection_ignores_incomparable_bounds() -> None:
    files = [FileBounds("bad.parquet", 100, {"value": "a"}, {"value": "z"})]

    assert find_boundary_files(files, [QueryRange("value", 1, 2)]) == []
