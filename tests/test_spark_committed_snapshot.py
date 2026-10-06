import gzip
import importlib
import json
import sys
import types
from typing import Any

import pytest

_BIG = "9223372036854775807"
_TABLE = "sample.demo.events"
_PREFIX = "[cryo-result] "

_SQL = {
    "optimize": (
        "CALL sample.system.rewrite_data_files(table => 'sample.demo.events', strategy => 'binpack')"
    ),
    "rewrite_position_deletes": (
        "CALL sample.system.rewrite_position_delete_files(table => 'sample.demo.events')"
    ),
    "expire_snapshots": (
        "ALTER TABLE sample.demo.events CREATE OR REPLACE TAG `cryo-protect-1` "
        "AS OF VERSION 1;\n"
        "CALL sample.system.expire_snapshots(table => 'sample.demo.events', retain_last => 1)"
    ),
    "rewrite_manifests": "CALL sample.system.rewrite_manifests(table => 'sample.demo.events')",
    "optimize_manifests": "CALL sample.system.rewrite_manifests(table => 'sample.demo.events')",
}


def _import_runner() -> Any:
    if "cryo_spark_runner" in sys.modules or "pyspark" in sys.modules:
        return importlib.import_module("cryo_spark_runner")
    pyspark = types.ModuleType("pyspark")
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = object  # type: ignore[attr-defined]
    pyspark.sql = sql  # type: ignore[attr-defined]
    sys.modules["pyspark"] = pyspark
    sys.modules["pyspark.sql"] = sql
    try:
        return importlib.import_module("cryo_spark_runner")
    finally:
        del sys.modules["pyspark"], sys.modules["pyspark.sql"]


class _Row:
    def __init__(self, **values: Any) -> None:
        self._values = values

    def asDict(self) -> dict[str, Any]:  # noqa: N802 - pyspark's name
        return self._values


class _Snapshot:
    def __init__(self, snapshot_id: int) -> None:
        self._snapshot_id = snapshot_id

    def snapshotId(self) -> int:  # noqa: N802 - Iceberg Java name
        return self._snapshot_id


class _Table:
    def __init__(self, snapshot_ids: list[int | None]) -> None:
        self._snapshot_ids = list(snapshot_ids)
        self.calls: list[str] = []

    def refresh(self) -> None:
        self.calls.append("refresh")

    def currentSnapshot(self) -> _Snapshot | None:  # noqa: N802 - Iceberg Java name
        self.calls.append("currentSnapshot")
        if not self._snapshot_ids:
            return None
        value = self._snapshot_ids.pop(0)
        if value is None:
            return None
        return _Snapshot(value)

    def files(self) -> None:
        raise AssertionError("listed data files")

    def manifests(self) -> None:
        raise AssertionError("listed manifests")

    def snapshots(self) -> None:
        raise AssertionError("listed snapshots")


class _Loader:
    def __init__(self, table: _Table, *, fail_on_load: int | None = None) -> None:
        self.table = table
        self.fail_on_load = fail_on_load
        self.names: list[str] = []

    def loadIcebergTable(self, session: object, name: str) -> _Table:  # noqa: N802
        self.names.append(name)
        if self.fail_on_load is not None and len(self.names) == self.fail_on_load:
            raise RuntimeError("metadata read failed")
        return self.table


class _Spark:
    def __init__(self, table: _Table, *, fail_on_load: int | None = None) -> None:
        self._jsparkSession = object()
        self.loader = _Loader(table, fail_on_load=fail_on_load)
        self._jvm = types.SimpleNamespace(
            org=types.SimpleNamespace(
                apache=types.SimpleNamespace(
                    iceberg=types.SimpleNamespace(
                        spark=types.SimpleNamespace(Spark3Util=self.loader)
                    )
                )
            )
        )
        self.executed: list[str] = []

    def sql(self, statement: str) -> Any:
        lowered = statement.lower()
        if any(token in lowered for token in (".files", ".manifests", ".snapshots")):
            raise AssertionError(f"listed metadata: {statement}")
        self.executed.append(statement)
        return types.SimpleNamespace(collect=lambda: [_Row(rewritten_data_files_count=3)])


def _manifest(tmp_path: Any, statements: list[dict[str, Any]], **table_extra: Any) -> str:
    path = tmp_path / "lane.json.gz"
    payload = {
        "tables": [{"table_id": "a", "statements": statements, **table_extra}],
    }
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    return str(path)


def _results(text: str) -> list[dict[str, Any]]:
    found = []
    for line in text.splitlines():
        if line.startswith(_PREFIX):
            found.append(json.loads(line[len(_PREFIX) :]))
    return found


@pytest.fixture(scope="module")
def runner() -> Any:
    return _import_runner()


@pytest.mark.parametrize("action", list(_SQL))
def test_changed_snapshot_id_is_a_decimal_string(
    runner: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    table = _Table([1, int(_BIG)])
    spark = _Spark(table)
    path = _manifest(tmp_path, [{"seq": 0, "action": action, "sql": _SQL[action]}])

    assert runner._run_manifest(spark, path, deadline_epoch=None) == 0

    captured = capsys.readouterr()
    [result] = _results(captured.out)
    metric = result["metrics"]["committed_snapshot_id"]
    assert result["ok"] is True
    assert metric == _BIG
    assert isinstance(metric, str)
    assert int(metric) > 2**53
    assert f'"committed_snapshot_id":"{_BIG}"' in captured.out
    assert spark.loader.names == [_TABLE, _TABLE]
    assert table.calls == ["refresh", "currentSnapshot", "refresh", "currentSnapshot"]
    assert not any(".snapshots" in sql or ".files" in sql for sql in spark.executed)


def test_unchanged_snapshot_id_leaves_the_key_absent(
    runner: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    table = _Table([int(_BIG), int(_BIG)])
    spark = _Spark(table)
    path = _manifest(tmp_path, [{"seq": 0, "action": "optimize", "sql": _SQL["optimize"]}])

    assert runner._run_manifest(spark, path, deadline_epoch=None) == 0

    [result] = _results(capsys.readouterr().out)
    assert result["ok"] is True
    assert "committed_snapshot_id" not in result["metrics"]
    assert spark.loader.names == [_TABLE, _TABLE]
    assert table.calls == ["refresh", "currentSnapshot", "refresh", "currentSnapshot"]


@pytest.mark.parametrize("fail_on_load", [1, 2])
def test_a_failed_snapshot_probe_leaves_the_statement_ok(
    runner: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str], fail_on_load: int
) -> None:
    spark = _Spark(_Table([1, int(_BIG)]), fail_on_load=fail_on_load)
    path = _manifest(tmp_path, [{"seq": 0, "action": "optimize", "sql": _SQL["optimize"]}])

    assert runner._run_manifest(spark, path, deadline_epoch=None) == 0

    captured = capsys.readouterr()
    [result] = _results(captured.out)
    assert result["ok"] is True
    assert "committed_snapshot_id" not in result["metrics"]
    assert "snapshot id probe failed" in captured.err
    assert any("rewrite_data_files" in sql for sql in spark.executed)


def test_non_committing_statements_are_not_probed(
    runner: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    spark = _Spark(_Table([int(_BIG), 2]))
    path = _manifest(
        tmp_path,
        [
            {
                "seq": 0,
                "action": "remove_orphan_files",
                "sql": "CALL sample.system.remove_orphan_files(table => 'sample.demo.events')",
            },
            {
                "seq": 1,
                "action": "convert_deletes",
                "sql": "CALL sample.system.rewrite_data_files(table => 'sample.demo.events')",
            },
            {
                "seq": 2,
                "action": "compute_statistics",
                "sql": "CALL sample.system.compute_table_stats(table => 'sample.demo.events')",
            },
        ],
    )

    assert runner._run_manifest(spark, path, deadline_epoch=None) == 0

    assert spark.loader.names == []
    for result in _results(capsys.readouterr().out):
        assert result["ok"] is True
        assert "committed_snapshot_id" not in result["metrics"]


def test_manifest_table_ref_is_used_when_sql_has_no_table_argument(
    runner: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    spark = _Spark(_Table([4, int(_BIG)]))
    path = _manifest(
        tmp_path,
        [{"seq": 0, "action": "optimize", "sql": "CALL sample.system.rewrite_data_files(t)"}],
        table_ref=_TABLE,
    )

    runner._run_manifest(spark, path, deadline_epoch=None)

    [result] = _results(capsys.readouterr().out)
    assert result["metrics"]["committed_snapshot_id"] == _BIG
    assert spark.loader.names == [_TABLE, _TABLE]


def test_snapshot_id_if_changed_keeps_only_a_new_decimal_string(runner: Any) -> None:
    assert runner._snapshot_id_if_changed("1", _BIG, before_ok=True, after_ok=True) == _BIG
    assert runner._snapshot_id_if_changed(None, _BIG, before_ok=True, after_ok=True) == _BIG
    assert runner._snapshot_id_if_changed(_BIG, _BIG, before_ok=True, after_ok=True) is None
    assert runner._snapshot_id_if_changed("1", _BIG, before_ok=False, after_ok=True) is None
    assert runner._snapshot_id_if_changed("1", _BIG, before_ok=True, after_ok=False) is None
    assert runner._snapshot_id_if_changed("1", None, before_ok=True, after_ok=True) is None


def test_decimal_snapshot_id_keeps_a_long_past_2_to_the_53(runner: Any) -> None:
    class _Long:
        def longValue(self) -> int:  # noqa: N802 - java.lang.Long
            return int(_BIG)

    assert runner._decimal_snapshot_id(int(_BIG)) == _BIG
    assert runner._decimal_snapshot_id(_BIG) == _BIG
    assert runner._decimal_snapshot_id(_Long()) == _BIG
    assert runner._decimal_snapshot_id(float(_BIG)) is None
    assert runner._decimal_snapshot_id(True) is None
    assert runner._decimal_snapshot_id(-5) == "-5"
