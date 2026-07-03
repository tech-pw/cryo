from __future__ import annotations

from cryo.ai.anomaly import detect_anomalies
from cryo.ai.copilot import list_verified_questions
from cryo.ai.explain import explain_health_change
from cryo.api.preview_cost import savings_projection_from_stats
from cryo.config import Settings
from cryo.domain.enums import Mode
from cryo.engines.native.payload import plan_to_payload
from cryo.events.consumer import InMemoryEventStream, TableCommitConsumer
from cryo.events.redis_stream import RedisEventStream
from cryo.events.watcher import InMemorySnapshotProbe, TableCommitWatcher
from cryo.planner.planner import build_plan


def test_anomaly_detects_spike() -> None:
    history = [
        {"total_delete_files": 10},
        {"total_delete_files": 12},
        {"total_delete_files": 11},
        {"total_delete_files": 100},
    ]
    anomalies = detect_anomalies(history, metrics=("total_delete_files",), z_threshold=2.0)
    assert anomalies
    assert anomalies[0].metric == "total_delete_files"


def test_explain_health_change_direction() -> None:
    text = explain_health_change(
        {"grade": "B", "overall_score": 80, "small_files_count": 100},
        {"grade": "C", "overall_score": 65, "small_files_count": 250},
    )
    assert "grade changed" in text.lower() or "remains" in text.lower()
    assert "Small file count" in text


def test_copilot_questions_list() -> None:
    questions = list_verified_questions()
    assert any(item["id"] == "worst_tables" for item in questions)


def test_native_payload_manifest_only() -> None:
    plan = build_plan({"schema_name": "s", "table_name": "t"}, [], Mode.MANIFEST_ONLY, {})
    payload = plan_to_payload(plan)
    assert payload["operations"][0]["type"] == "optimize_manifests"


def test_event_watcher_publishes_on_snapshot_change() -> None:
    stream = InMemoryEventStream()
    consumer = TableCommitConsumer(stream)
    probe = InMemorySnapshotProbe({"iceberg.raw.events": 5})
    watcher = TableCommitWatcher(probe, consumer.publish)
    watcher.watch("iceberg.raw.events")
    watcher.poll_once()
    probe.commit("iceberg.raw.events", 6)
    events = watcher.poll_once()
    assert len(events) == 1
    assert consumer.consume_once().handled == 1


def test_savings_projection_from_stats() -> None:
    stats = {
        "small_files_count": 100,
        "raw": {
            "live_bytes": 1_000_000_000,
            "metadata_bytes": 50_000_000,
            "reclaim_optimize_bytes": 200_000_000,
        },
    }
    projection = savings_projection_from_stats(stats, Settings())
    assert projection.projected_savings_usd_month >= 0


def test_redis_event_stream_interface() -> None:
    assert hasattr(RedisEventStream, "publish")
