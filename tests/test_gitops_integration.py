"""Integration tests for GitOps policy loading."""

from __future__ import annotations

from pathlib import Path

from cryo.gitops.policy_loader import (
    GitOpsPolicyConfig,
    load_policies_from_directory,
    parse_policy_yaml,
)

SAMPLE_POLICY = """
table: iceberg.finance.transactions
maintenance:
  optimize:
    enabled: true
    cooldown_days: 3
    strategy: zorder
    zorder_columns: [customer_id, transaction_date]
  expire_snapshots:
    enabled: true
    retention_days: 30
alerts:
  slack_webhook: https://hooks.slack.com/services/TEST
"""


def test_parse_policy_yaml_extracts_table_and_maintenance():
    """parse_policy_yaml should map YAML fields into GitOpsPolicyConfig."""
    policy = parse_policy_yaml(SAMPLE_POLICY)

    assert isinstance(policy, GitOpsPolicyConfig)
    assert policy.table_pattern == "iceberg.finance.transactions"
    assert policy.maintenance["optimize"]["enabled"] is True
    assert policy.maintenance["optimize"]["zorder_columns"] == [
        "customer_id",
        "transaction_date",
    ]
    assert policy.alerts is not None
    assert "slack_webhook" in policy.alerts


def test_load_policies_from_directory_reads_yaml_files(tmp_path: Path):
    """load_policies_from_directory should load all *.yaml files recursively."""
    finance_dir = tmp_path / "finance"
    finance_dir.mkdir()
    (finance_dir / "transactions.yaml").write_text(SAMPLE_POLICY)
    (tmp_path / "events.yaml").write_text(
        """
table: iceberg.analytics.events
maintenance:
  optimize:
    enabled: true
    cooldown_days: 7
"""
    )

    policies = load_policies_from_directory(tmp_path)

    assert len(policies) == 2
    patterns = {p.table_pattern for p in policies}
    assert "iceberg.finance.transactions" in patterns
    assert "iceberg.analytics.events" in patterns


def test_load_policies_from_directory_skips_invalid_files(tmp_path: Path):
    """Invalid YAML files should be skipped without aborting the load."""
    (tmp_path / "valid.yaml").write_text(SAMPLE_POLICY)
    (tmp_path / "broken.yaml").write_text("table: [unclosed")

    policies = load_policies_from_directory(tmp_path)

    assert len(policies) == 1
    assert policies[0].table_pattern == "iceberg.finance.transactions"


def detect_policy_conflicts(policies: list[GitOpsPolicyConfig]) -> list[str]:
    """Helper: detect duplicate exact table patterns (conflict detection not in loader)."""
    seen: dict[str, int] = {}
    conflicts: list[str] = []
    for policy in policies:
        pattern = policy.table_pattern
        seen[pattern] = seen.get(pattern, 0) + 1
        if seen[pattern] > 1:
            conflicts.append(pattern)
    return conflicts


def test_policy_conflict_detection_finds_duplicate_patterns():
    """Duplicate table patterns across policies should be flagged as conflicts."""
    policy_a = parse_policy_yaml(SAMPLE_POLICY)
    policy_b = parse_policy_yaml(
        """
table: iceberg.finance.transactions
maintenance:
  optimize:
    enabled: false
"""
    )
    policy_c = parse_policy_yaml(
        """
table: iceberg.analytics.events
maintenance:
  optimize:
    enabled: true
"""
    )

    conflicts = detect_policy_conflicts([policy_a, policy_b, policy_c])

    assert conflicts == ["iceberg.finance.transactions"]


def test_parse_policy_yaml_requires_table_key():
    """Missing table key should yield empty pattern rather than raising."""
    policy = parse_policy_yaml("maintenance:\n  optimize:\n    enabled: true\n")

    assert policy.table_pattern == ""
    assert policy.maintenance["optimize"]["enabled"] is True
