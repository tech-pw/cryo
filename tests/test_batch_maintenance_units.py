"""Pure pieces of the batch maintenance pipeline (cryo/maintenance/)."""

from __future__ import annotations

import base64
import gzip
import importlib
import json
import sys
import types
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from cryo.engines.spark.k8s import SparkK8sConfig
from cryo.maintenance import results as results_mod
from cryo.maintenance.k8s_lanes import LaneRunner, LaneSpec, build_lane_objects
from cryo.maintenance.lanes import LaneLimits, LaneTable, lane_count, plan_lanes
from cryo.maintenance.manifest import (
    DEFAULT_PARAMS,
    TableData,
    build_table_work,
    lane_manifest_bytes,
)
from cryo.maintenance.results import parse_lane_log
from cryo.maintenance.selection import Candidate, rank_candidates
from cryo.planner.guardrails import CORRECT_ORDER

# -- result parsing ------------------------------------------------------------------------


def _result_line(table_id: uuid.UUID, seq: int, **extra: Any) -> str:
    payload = {"table_id": str(table_id), "seq": seq, "action": "optimize", "ok": True, **extra}
    return results_mod.RESULT_PREFIX + json.dumps(payload)


def test_parse_lane_log_reads_results_starts_and_lane_done_among_spark_noise() -> None:
    table = uuid.uuid4()
    log = "\n".join(
        [
            "26/09/24 19:00:01 WARN NativeCodeLoader: Unable to load native-hadoop library",
            results_mod.TABLE_START_PREFIX + json.dumps({"table_id": str(table)}),
            _result_line(table, 0, duration_ms=12, rows=1, metrics={"added_data_files_count": 3}),
            "[cryo-result] {not json",
            results_mod.LANE_DONE_PREFIX + json.dumps({"tables": 1, "succeeded": 1}),
        ]
    )

    parsed = parse_lane_log(log)

    assert parsed.started_tables == [table]
    assert [(r.table_id, r.seq, r.ok) for r in parsed.results] == [(table, 0, True)]
    assert parsed.results[0].metrics == {"added_data_files_count": 3}
    assert parsed.lane_done == {"tables": 1, "succeeded": 1}


def test_parse_lane_log_keeps_a_line_whose_error_contains_unicode_line_separators() -> None:
    """str.splitlines() would split on U+0085 inside the JSON string and drop the result."""
    table = uuid.uuid4()
    line = results_mod.RESULT_PREFIX + json.dumps(
        {"table_id": str(table), "seq": 2, "action": "optimize", "ok": False, "error": "a\x85b"},
        ensure_ascii=False,
    )

    parsed = parse_lane_log(line)

    assert len(parsed.results) == 1
    assert parsed.results[0].error == "a\x85b"


def test_runner_and_parser_agree_on_line_prefixes() -> None:
    runner = _import_runner()
    assert runner.RESULT_PREFIX == results_mod.RESULT_PREFIX
    assert runner.TABLE_START_PREFIX == results_mod.TABLE_START_PREFIX
    assert runner.LANE_DONE_PREFIX == results_mod.LANE_DONE_PREFIX


# -- lanes ---------------------------------------------------------------------------------


def _tables(*gbs: float) -> list[LaneTable]:
    return [LaneTable(uuid.uuid4(), gb) for gb in gbs]


def test_a_quiet_day_gets_one_small_lane() -> None:
    lanes = plan_lanes(_tables(0.5, 1.0, 2.0))

    assert len(lanes) == 1
    assert (lanes[0].cpu_milli, lanes[0].mem_mib) == (1000, 4096)


def test_lane_count_follows_work_and_is_capped() -> None:
    limits = LaneLimits(max_lanes=4, target_gb_per_lane=100)

    assert lane_count(_tables(10, 10), limits) == 1
    assert lane_count(_tables(150, 150), limits) == 2
    assert lane_count(_tables(*([500.0] * 10)), limits) == 4


def test_lane_is_sized_by_its_largest_table_and_never_above_the_ceiling() -> None:
    limits = LaneLimits(max_lanes=2, max_cpu_milli=3000, max_mem_mib=12288, target_gb_per_lane=10)
    lanes = plan_lanes(_tables(900, 2), limits)

    big = next(lane for lane in lanes if lane.largest_table_gb == 900)
    small = next(lane for lane in lanes if lane.largest_table_gb == 2)
    assert (big.cpu_milli, big.mem_mib) == (3000, 12288)
    assert (small.cpu_milli, small.mem_mib) == (1000, 4096)


def test_packing_keeps_rank_order_within_each_lane() -> None:
    tables = _tables(300, 250, 200, 5, 4, 3)
    lanes = plan_lanes(tables, LaneLimits(max_lanes=2, target_gb_per_lane=100))
    rank = {t.table_id: i for i, t in enumerate(tables)}

    for lane in lanes:
        positions = [rank[t.table_id] for t in lane.tables]
        assert positions == sorted(positions)
    assert sum(len(lane.tables) for lane in lanes) == len(tables)


# -- ranking -------------------------------------------------------------------------------


def _candidate(**overrides: Any) -> Candidate:
    base: dict[str, Any] = {
        "table_id": uuid.uuid4(),
        "schema_name": "s",
        "table_name": "t",
        "total_size_gb": 10.0,
        "total_files": 1000,
        "small_files": 500,
        "delete_file_pct": 0.0,
        "query_count_30d": 100,
        "projected_savings_usd_month": 1.0,
        "needs_compaction": True,
    }
    base.update(overrides)
    return Candidate(**base)


def test_ineligible_tables_are_dropped_unless_explicitly_requested() -> None:
    healthy = _candidate(needs_compaction=False, small_files=0, delete_file_pct=0.0)
    sick = _candidate()

    assert [r.candidate for r in rank_candidates([healthy, sick], limit=10)] == [sick]
    assert len(rank_candidates([healthy, sick], limit=10, require_eligible=False)) == 2


def test_delete_heavy_table_is_eligible_without_small_files() -> None:
    mor = _candidate(needs_compaction=False, small_files=0, delete_file_pct=5.0)

    assert rank_candidates([mor], limit=10)


def test_hot_needy_table_outranks_cold_one_and_limit_applies() -> None:
    hot = _candidate(table_name="hot", query_count_30d=50_000)
    cold = _candidate(table_name="cold", query_count_30d=0, small_files=100)
    middle = _candidate(table_name="middle", query_count_30d=500)

    ranked = rank_candidates([cold, middle, hot], limit=2)

    assert [r.candidate.table_name for r in ranked] == ["hot", "middle"]


# -- manifest ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Destination:
    engine: str = "spark_emr"
    config: dict[str, Any] = field(default_factory=lambda: {"catalog": "cdp"})


_STATS = {
    "catalog": "cdp",
    "schema_name": "demo",
    "table_name": "events",
    "small_files_count": 500,
    "total_files": 600,
    "total_size_gb": 10,
}


def _work(*, shared: bool = False, params: dict[str, Any] | None = None) -> Any:
    return build_table_work(
        uuid.uuid4(),
        load_table=lambda _tid: TableData(stats=_STATS, partitions=[]),
        destination=_Destination(),
        params=params or DEFAULT_PARAMS,
        protected_snapshot_ids=lambda _tid: [],
        has_shared_location=lambda _tid: shared,
    )


def test_table_work_runs_every_needed_operation_in_canonical_order() -> None:
    work = _work()

    actions = [s.action for s in work.statements]
    order = [op.value for op in CORRECT_ORDER]
    assert actions == sorted(actions, key=order.index)
    assert {"optimize", "compute_statistics", "expire_snapshots", "remove_orphan_files"} <= set(
        actions
    )
    assert all(s.sql.startswith(("ALTER", "CALL")) for s in work.statements)
    assert work.table_ref == "cdp.demo.events"


def test_shared_location_table_gets_no_orphan_removal() -> None:
    assert "remove_orphan_files" not in [s.action for s in _work(shared=True).statements]


def test_skip_operations_param_drops_orphan_removal_for_a_closed_gate() -> None:
    params = {**DEFAULT_PARAMS, "skip_operations": ["remove_orphan_files"]}

    assert "remove_orphan_files" not in [s.action for s in _work(params=params).statements]


def test_lane_manifest_is_reproducible_gzip_json() -> None:
    work = _work()
    run_id = uuid.uuid4()

    first = lane_manifest_bytes(run_id, 0, [work])
    assert first == lane_manifest_bytes(run_id, 0, [work])
    payload = json.loads(gzip.decompress(first))
    assert payload["tables"][0]["table_ref"] == "cdp.demo.events"
    assert [s["seq"] for s in payload["tables"][0]["statements"]] == list(
        range(len(work.statements))
    )


# -- lane Kubernetes objects ----------------------------------------------------------------


def _cfg(**overrides: Any) -> SparkK8sConfig:
    base: dict[str, Any] = {
        "namespace": "cryo",
        "image": "registry/cryo-spark@sha256:abc",
        "service_account": "cryo-spark",
        "catalog_impl": "hive",
        "catalog_uri": "thrift://metastore:9083",
        "catalog_warehouse": "s3://bucket/",
        "node_selector": {"karpenter.sh/capacity-type": "on-demand"},
    }
    base.update(overrides)
    return SparkK8sConfig(**base)


def _spec(index: int = 0) -> LaneSpec:
    return LaneSpec(index=index, cpu_milli=2000, mem_mib=8192, manifest=b"\x1f\x8bfake")


def test_lane_objects_run_local_mode_sized_to_the_lane() -> None:
    run_id = uuid.uuid4()
    secret, job = build_lane_objects(
        run_id,
        _spec(),
        _cfg(),
        catalog="cdp",
        deadline_epoch=1_800_000_000,
        active_deadline_seconds=3600,
    )

    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    command = container["command"]
    assert command[command.index("--master") + 1] == "local[2]"
    assert command[command.index("--driver-memory") + 1] == f"{int(8192 * 0.7)}m"
    assert command[command.index("--catalog") + 1] == "cdp"
    assert command[command.index("--deadline-epoch") + 1] == "1800000000"
    assert container["resources"]["requests"] == container["resources"]["limits"]
    assert container["resources"]["limits"] == {"cpu": "2000m", "memory": "8192Mi"}
    assert job["spec"]["backoffLimit"] == 0
    assert pod["restartPolicy"] == "Never"
    assert pod["nodeSelector"] == {"karpenter.sh/capacity-type": "on-demand"}
    assert job["spec"]["template"]["metadata"]["annotations"] == {
        "karpenter.sh/do-not-disrupt": "true"
    }
    assert job["metadata"]["labels"]["cryo/maintenance-run"] == str(run_id)
    assert base64.b64decode(secret["data"]["manifest.json.gz"]) == b"\x1f\x8bfake"


def test_s3_keys_reach_the_pod_only_through_secret_refs() -> None:
    secret, job = build_lane_objects(
        uuid.uuid4(),
        _spec(),
        _cfg(s3_access_key="AKIAEXAMPLE", s3_secret_key="shh-example"),
        catalog="cdp",
        deadline_epoch=0,
        active_deadline_seconds=600,
    )

    assert "AKIAEXAMPLE" not in json.dumps(job)
    assert "shh-example" not in json.dumps(job)
    env = {e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["AWS_ACCESS_KEY_ID"]["valueFrom"]["secretKeyRef"]["key"] == "access_key_id"
    assert base64.b64decode(secret["data"]["secret_access_key"]) == b"shh-example"


def test_no_keys_means_no_aws_env_so_the_service_account_role_applies() -> None:
    _, job = build_lane_objects(
        uuid.uuid4(), _spec(), _cfg(), catalog="cdp", deadline_epoch=0, active_deadline_seconds=600
    )

    assert job["spec"]["template"]["spec"]["containers"][0]["env"] == []


def _pod(name: str, phase: str, *, exit_code: int | None = None) -> SimpleNamespace:
    terminated = (
        SimpleNamespace(reason="OOMKilled", exit_code=exit_code) if exit_code is not None else None
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(
            phase=phase,
            reason=None,
            container_statuses=[SimpleNamespace(state=SimpleNamespace(terminated=terminated))],
        ),
    )


def test_lane_runner_reports_each_result_once_even_after_log_rotation() -> None:
    table_a, table_b = uuid.uuid4(), uuid.uuid4()
    core, batch = MagicMock(), MagicMock()
    runner = LaneRunner(uuid.uuid4(), _cfg(), catalog="cdp", batch_api=batch, core_api=core)
    runner.launch([_spec()], deadline_epoch=0, active_deadline_seconds=600)
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod("p0", "Running")])
    core.read_namespaced_pod_log.return_value = "\n".join(
        [_result_line(table_a, 0), _result_line(table_a, 1)]
    )

    first = runner.poll()
    # Rotation: the API now returns only the newest file, which repeats nothing old.
    core.read_namespaced_pod_log.return_value = _result_line(table_b, 0)
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod("p0", "Succeeded")])
    second = runner.poll()

    assert [(r.table_id, r.seq) for _, r in first.new_results] == [(table_a, 0), (table_a, 1)]
    assert [(r.table_id, r.seq) for _, r in second.new_results] == [(table_b, 0)]
    assert runner.all_finished


def test_lane_runner_records_why_a_lane_pod_died() -> None:
    core = MagicMock()
    runner = LaneRunner(uuid.uuid4(), _cfg(), catalog="cdp", batch_api=MagicMock(), core_api=core)
    runner.launch([_spec()], deadline_epoch=0, active_deadline_seconds=600)
    core.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_pod("p0", "Failed", exit_code=137)]
    )
    core.read_namespaced_pod_log.return_value = ""

    update = runner.poll()

    assert update.newly_finished[0].exit_reason == "OOMKilled (exit 137)"


def test_teardown_deletes_jobs_secrets_and_leftover_pods_and_tolerates_404() -> None:
    core, batch = MagicMock(), MagicMock()
    run_id = uuid.uuid4()
    runner = LaneRunner(run_id, _cfg(), catalog="cdp", batch_api=batch, core_api=core)
    runner.launch([_spec(0), _spec(1)], deadline_epoch=0, active_deadline_seconds=600)
    missing = Exception("gone")
    missing.status = 404  # type: ignore[attr-defined]
    batch.delete_namespaced_job.side_effect = [missing, None]
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod("stray", "Running")])

    runner.teardown()

    assert batch.delete_namespaced_job.call_count == 2
    assert core.delete_namespaced_secret.call_count == 2
    core.list_namespaced_pod.assert_called_with(
        namespace="cryo", label_selector=f"cryo/maintenance-run={run_id}"
    )
    core.delete_namespaced_pod.assert_called_once()


# -- runner manifest mode (pyspark stubbed) --------------------------------------------------


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
        # Don't leave a fake pyspark behind for tests that probe for the real one.
        del sys.modules["pyspark"], sys.modules["pyspark.sql"]


class _Row:
    def __init__(self, **values: Any) -> None:
        self._values = values

    def asDict(self) -> dict[str, Any]:  # noqa: N802 - pyspark's name
        return self._values


class _FakeSpark:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.executed: list[str] = []

    def sql(self, statement: str) -> Any:
        self.executed.append(statement)
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError(f"boom: {statement}")
        return SimpleNamespace(collect=lambda: [_Row(rewritten_data_files_count=7)])


def _manifest(tmp_path: Any, tables: list[dict[str, Any]]) -> str:
    path = tmp_path / "m.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"tables": tables}).encode()))
    return str(path)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    return [
        json.loads(line.split("] ", 1)[1])
        for line in out.splitlines()
        if line.startswith("[cryo-result] ")
    ]


def test_runner_splits_multi_part_statements_and_isolates_failures_per_table(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _import_runner()
    spark = _FakeSpark(fail_on="rewrite_manifests")
    path = _manifest(
        tmp_path,
        [
            {
                "table_id": "a",
                "statements": [
                    {"seq": 0, "action": "optimize", "sql": "ALTER TABLE t SET X;\nCALL opt(t)"},
                    {"seq": 1, "action": "optimize_manifests", "sql": "CALL rewrite_manifests(t)"},
                    {"seq": 2, "action": "expire_snapshots", "sql": "CALL expire(t)"},
                ],
            },
            {"table_id": "b", "statements": [{"seq": 0, "action": "optimize", "sql": "CALL o(b)"}]},
        ],
    )

    assert runner._run_manifest(spark, path, deadline_epoch=None) == 0

    assert spark.executed[:2] == ["ALTER TABLE t SET X", "CALL opt(t)"]
    assert "CALL expire(t)" not in spark.executed
    results = _lines(capsys)
    by_key = {(r["table_id"], r["seq"]): r for r in results}
    assert by_key[("a", 0)]["ok"] and by_key[("a", 0)]["metrics"] == {
        "rewritten_data_files_count": 7
    }
    assert not by_key[("a", 1)]["ok"] and "boom" in by_key[("a", 1)]["error"]
    assert by_key[("a", 2)]["skipped"]
    assert by_key[("b", 0)]["ok"]


def test_runner_starts_no_table_after_the_deadline(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _import_runner()
    spark = _FakeSpark()
    path = _manifest(
        tmp_path,
        [{"table_id": "a", "statements": [{"seq": 0, "action": "optimize", "sql": "CALL o(a)"}]}],
    )

    runner._run_manifest(spark, path, deadline_epoch=1.0)

    assert spark.executed == []
    [result] = _lines(capsys)
    assert result["skipped"] and "deadline" in result["error"]
