from __future__ import annotations

from cryo.engines.k8s_cost_labels import (
    ENGINE_LABEL,
    OPERATION_LABEL,
    WORKLOAD_NAME_LABEL,
    WORKLOAD_TYPE_LABEL,
    cost_attribution_labels,
    k8s_label_value,
    operation_from_native_payload,
)


def test_cost_attribution_labels_are_cur_safe() -> None:
    labels = cost_attribution_labels(
        workload_name="cryo-native",
        engine="native",
        operation="rewrite_data_files",
    )
    assert labels[WORKLOAD_NAME_LABEL] == "cryo-native"
    assert labels[WORKLOAD_TYPE_LABEL] == "Job"
    assert labels[ENGINE_LABEL] == "native"
    assert labels[OPERATION_LABEL] == "rewrite_data_files"
    for key, value in labels.items():
        assert "/" not in key
        assert len(key) <= 63
        assert len(value) <= 63


def test_k8s_label_value_strips_illegal_chars() -> None:
    assert k8s_label_value(" Optimize Manifests ") == "optimize-manifests"


def test_operation_from_native_payload_single_and_mixed() -> None:
    assert operation_from_native_payload({"operations": [{"type": "rewrite_data_files"}]}) == (
        "cryo-native",
        "native",
        "rewrite_data_files",
    )
    assert operation_from_native_payload(
        {
            "batch": True,
            "tables": [
                {"operations": [{"type": "expire_snapshots"}]},
                {"operations": [{"type": "rewrite_manifests"}]},
            ],
        }
    ) == ("cryo-native", "native", "expire_snapshots-rewrite_manifests")


def test_operation_from_native_payload_scan_command() -> None:
    assert operation_from_native_payload(
        {}, command=["python", "-m", "cryo.scanner.job_runner"]
    ) == ("cryo-scanner", "scanner", "scan")
