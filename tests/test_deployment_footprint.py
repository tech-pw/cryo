"""Tests for scripts/deployment_footprint.py's Helm-chart-derived resource footprint."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.deployment_footprint import (
    AMORO_PUBLISHED,
    BASELINE_COMPONENTS,
    DEFAULT_VALUES_PATH,
    ComponentFootprint,
    _parse_cpu_milli,
    _parse_memory_mib,
    load_component_footprints,
    render_report,
)


def test_default_values_path_points_at_the_real_chart() -> None:
    """The script's whole point is reading the real, live chart -- not a fixture. Confirm the
    default path actually resolves to the committed file."""
    assert DEFAULT_VALUES_PATH.exists()
    assert DEFAULT_VALUES_PATH.name == "values.yaml"


def test_parse_cpu_milli_handles_millicore_and_core_strings() -> None:
    assert _parse_cpu_milli("250m") == 250
    assert _parse_cpu_milli("1") == 1000
    assert _parse_cpu_milli("2") == 2000
    assert _parse_cpu_milli(500) == 500


def test_parse_memory_mib_handles_gi_and_mi_strings() -> None:
    assert _parse_memory_mib("768Mi") == 768
    assert _parse_memory_mib("1Gi") == 1024
    assert _parse_memory_mib("4Gi") == 4096
    assert _parse_memory_mib("512Mi") == 512


def test_component_footprint_totals_multiply_by_replicas() -> None:
    footprint = ComponentFootprint(
        name="worker", replicas=3, request_cpu_milli=500, request_mem_mib=1024
    )
    assert footprint.total_cpu_milli == 1500
    assert footprint.total_mem_mib == 3072


def test_load_component_footprints_against_the_real_chart_matches_committed_defaults() -> None:
    """A live read of deploy/helm/cryo/values.yaml -- this test breaks (loudly, which is the
    point) if someone changes a resource request without updating this footprint's
    expectations, keeping the claim self-verifying rather than silently stale."""
    footprints = load_component_footprints(DEFAULT_VALUES_PATH, BASELINE_COMPONENTS)
    by_name = {f.name: f for f in footprints}

    assert by_name["api"].replicas == 2
    assert by_name["api"].request_cpu_milli == 250
    assert by_name["api"].request_mem_mib == 768

    assert by_name["worker"].replicas == 2
    assert by_name["worker"].request_cpu_milli == 500
    assert by_name["worker"].request_mem_mib == 1024

    assert by_name["governor"].replicas == 1
    assert by_name["scheduler"].replicas == 1

    # scannerWorker: autoscaling.enabled=true in the committed chart -> minReplicas wins over
    # the fallback `replicas` field, since KEDA/HPA is the steady-state floor in practice.
    assert by_name["scannerWorker"].replicas == 3


def test_load_component_footprints_prefers_autoscaling_min_replicas_when_enabled(
    tmp_path: Path,
) -> None:
    values = {
        "scannerWorker": {
            "replicas": 8,
            "resources": {"requests": {"cpu": "500m", "memory": "512Mi"}},
            "autoscaling": {"enabled": True, "minReplicas": 3},
        }
    }
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.dump(values))

    footprints = load_component_footprints(values_path, ("scannerWorker",))

    assert footprints[0].replicas == 3  # minReplicas, not the fallback `replicas: 8`


def test_load_component_footprints_falls_back_to_replicas_when_autoscaling_disabled(
    tmp_path: Path,
) -> None:
    values = {
        "scannerWorker": {
            "replicas": 3,
            "resources": {"requests": {"cpu": "500m", "memory": "512Mi"}},
            "autoscaling": {"enabled": False, "minReplicas": 8},
        }
    }
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.dump(values))

    footprints = load_component_footprints(values_path, ("scannerWorker",))

    assert footprints[0].replicas == 3  # autoscaling disabled -> plain `replicas` wins


def test_load_component_footprints_raises_on_missing_component(tmp_path: Path) -> None:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(
        yaml.dump({"api": {"resources": {"requests": {"cpu": "250m", "memory": "768Mi"}}}})
    )

    with pytest.raises(KeyError, match="worker"):
        load_component_footprints(values_path, ("api", "worker"))


def test_load_component_footprints_raises_on_missing_resource_requests(tmp_path: Path) -> None:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.dump({"api": {"replicas": 1}}))

    with pytest.raises(KeyError, match="resources.requests"):
        load_component_footprints(values_path, ("api",))


def test_amoro_published_is_empty_no_fabricated_numbers() -> None:
    """The whole point of this benchmark is not inventing a competitor number it can't back
    up -- see the module docstring. This must stay empty until a real citation is added to
    docs/amoro-evaluation.md §7 and wired in deliberately."""
    assert AMORO_PUBLISHED == {}


def test_render_report_text_says_amoro_comparison_not_included_when_empty() -> None:
    footprints = [
        ComponentFootprint(name="api", replicas=1, request_cpu_milli=250, request_mem_mib=768)
    ]
    report = render_report(footprints, as_json=False)
    assert "NOT INCLUDED" in report
    assert "fabricating one here would misrepresent" in report


def test_render_report_json_totals_match_component_sums() -> None:
    footprints = [
        ComponentFootprint(name="api", replicas=2, request_cpu_milli=250, request_mem_mib=768),
        ComponentFootprint(name="worker", replicas=1, request_cpu_milli=500, request_mem_mib=1024),
    ]
    import json

    payload = json.loads(render_report(footprints, as_json=True))

    assert payload["cryo"]["total_cpu_cores"] == 1.0  # (2*250 + 1*500) / 1000
    assert payload["cryo"]["total_mem_gib"] == pytest.approx(2.5, abs=0.01)  # (2*768+1024)/1024
    assert payload["amoro_published"]["components"] == {}
