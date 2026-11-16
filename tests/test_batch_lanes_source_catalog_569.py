"""Batch lanes take their catalog URI, warehouse and S3 keys from each table's source (#569)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from cryo.db.models import Destination, MaintenanceJob, Source, Table, TableStats
from cryo.engines.spark.k8s import SparkK8sConfig, apply_source_catalog_config
from cryo.engines.spark.k8s_engine import SparkK8sEngine
from cryo.maintenance.k8s_lanes import spark_submit_command
from tests.test_batch_maintenance_coordinator import NOW, _harness, _seed

HMS = "thrift://metastore.example:9083"
OTHER_HMS = "thrift://other-metastore.example:9083"
DEST_CONFIG = {"catalog": "sample", "spark_k8s": {"namespace": "cryo", "catalog_impl": "hive"}}


def _set_source(session_factory: Any, config: dict[str, Any]) -> None:
    with session_factory() as session:
        source = session.scalars(select(Source)).one()
        source.config = config
        destination = session.scalars(select(Destination)).one()
        destination.config = DEST_CONFIG
        session.commit()


def _add_source_with_table(session_factory: Any, config: dict[str, Any]) -> uuid.UUID:
    with session_factory() as session:
        source = Source(id=uuid.uuid4(), name="second", type="hive", config=config, enabled=True)
        session.add(source)
        session.flush()
        table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="demo",
            table_name="o0",
            is_active=True,
        )
        session.add(table)
        session.flush()
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=NOW - timedelta(hours=6),
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="D",
                overall_score=40,
                total_files=1000,
                small_files_count=950,
                total_size_gb=12.0,
                query_count_30d=5000,
            )
        )
        session.commit()
        return table.id


def _lane_configs(runner: Any) -> dict[str, SparkK8sConfig]:
    """table_id -> the config its lane was launched with."""
    out: dict[str, SparkK8sConfig] = {}
    for lane in runner.lanes:
        assert lane.spec.cfg is not None
        for table in runner.manifests[lane.spec.index]["tables"]:
            out[table["table_id"]] = lane.spec.cfg
    return out


def test_a_destination_without_a_uri_gets_the_source_uri(session_factory) -> None:
    ids = _seed(session_factory, tables=2)
    _set_source(session_factory, {"uri": HMS, "warehouse": "s3://lake/wh"})
    h = _harness(session_factory)

    h.coordinator.run(trigger="batch-cron")

    configs = _lane_configs(h.runners[0])
    assert set(configs) == {str(i) for i in ids}
    for cfg in configs.values():
        assert cfg.catalog_uri == HMS
        assert cfg.catalog_warehouse == "s3://lake/wh"
        lane = h.runners[0].lanes[0].spec
        command = spark_submit_command(lane, cfg, catalog="sample", deadline_epoch=0)
        assert command[command.index("--catalog-uri") + 1] == HMS


def test_two_sources_get_their_own_lanes_and_keys(session_factory) -> None:
    ids = _seed(session_factory, tables=2)
    _set_source(
        session_factory,
        {"uri": HMS, "s3.access-key-id": "AK1", "s3.secret-access-key": "SK1"},
    )
    other = _add_source_with_table(
        session_factory,
        {"uri": OTHER_HMS, "s3.access-key-id": "AK2", "s3.secret-access-key": "SK2"},
    )
    h = _harness(session_factory)

    h.coordinator.run(trigger="batch-cron")

    configs = _lane_configs(h.runners[0])
    assert {configs[str(i)].catalog_uri for i in ids} == {HMS}
    assert {configs[str(i)].s3_access_key for i in ids} == {"AK1"}
    assert configs[str(other)].catalog_uri == OTHER_HMS
    assert configs[str(other)].s3_access_key == "AK2"
    lanes_by_uri: dict[str | None, set[int]] = {}
    for lane in h.runners[0].lanes:
        assert lane.spec.cfg is not None
        lanes_by_uri.setdefault(lane.spec.cfg.catalog_uri, set()).add(lane.spec.index)
    assert not lanes_by_uri[HMS] & lanes_by_uri[OTHER_HMS]


def test_destination_keys_still_win_over_source_keys() -> None:
    base = SparkK8sConfig.from_config(
        {"spark_k8s": {"s3_access_key": "DEST", "s3_secret_key": "DESTSK"}}
    )
    cfg = apply_source_catalog_config(
        base, {"uri": HMS, "s3.access-key-id": "SRC", "s3.secret-access-key": "SRCSK"}
    )
    assert (cfg.s3_access_key, cfg.catalog_uri) == ("DEST", HMS)


def test_worker_and_coordinator_build_the_same_catalog_config(session_factory) -> None:
    ids = _seed(session_factory, tables=1)
    source_config = {
        "uri": HMS,
        "warehouse": "s3://lake/wh",
        "s3.access-key-id": "AK1",
        "s3.secret-access-key": "SK1",
    }
    _set_source(session_factory, source_config)
    h = _harness(session_factory)

    worker_cfg = SparkK8sEngine(config=DEST_CONFIG).with_source_config(source_config)._k8s_config
    batch_cfgs, errors = h.coordinator._source_configs(SparkK8sConfig.from_config(DEST_CONFIG), ids)

    assert errors == {}
    assert batch_cfgs[ids[0]] == worker_cfg


def test_a_table_with_no_source_fails_with_one_clear_error(session_factory) -> None:
    _seed(session_factory, tables=1)
    h = _harness(session_factory)
    stray = uuid.uuid4()

    configs, errors = h.coordinator._source_configs(
        SparkK8sConfig.from_config(DEST_CONFIG), [stray]
    )

    assert configs == {}
    assert "no source" in errors[stray]


def test_every_job_records_a_lane_when_sources_resolve(session_factory) -> None:
    _seed(session_factory, tables=2)
    _set_source(session_factory, {"uri": HMS})
    h = _harness(session_factory)

    h.coordinator.run(trigger="batch-cron")

    with session_factory() as session:
        jobs = list(session.scalars(select(MaintenanceJob)))
    assert jobs and all(job.engine_ref for job in jobs)
