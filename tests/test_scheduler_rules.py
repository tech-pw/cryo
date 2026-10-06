from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.domain.enums import Mode
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableAssignment,
    TableCandidate,
    effective_preset,
    evaluate_predicate,
    tables_for_preset,
)


def test_evaluate_predicate_supports_shared_filter_grammar() -> None:
    row = {
        "schema_name": "mview_sales",
        "table_name": "orders_daily",
        "total_size_gb": 250,
        "small_files_count": 1_200,
        "grade": "F",
    }

    assert evaluate_predicate(
        {
            "all": [
                {"field": "schema_name", "op": "glob", "value": "mview_*"},
                {"field": "total_size_gb", "op": ">", "value": 100},
                {"field": "grade", "op": "in", "value": ["D", "F"]},
                {"not": {"field": "table_name", "op": "like", "value": "%tmp%"}},
            ]
        },
        row,
    )


def test_effective_preset_falls_back_to_default_when_unconfigured() -> None:
    # Product decision 2026-09-29: tables without an assignment or matching rule fall
    # through to the tenant's is_default preset (provenance="default") instead of staying
    # unmaintained.
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        mode=Mode.DEEP,
        priority=0,
        is_default=True,
    )
    table = TableCandidate("table-1", "demo", "orders", {"total_size_gb": 5})
    effective = effective_preset(table, [default_native], rules=())
    assert effective is not None
    assert effective.preset.id == "default-native"
    assert effective.provenance == "default"


def test_effective_preset_returns_none_when_no_default_and_unconfigured() -> None:
    # With no is_default preset in scope at all, an unmatched table still resolves to None.
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    table = TableCandidate("table-1", "demo", "orders", {"total_size_gb": 5})
    effective = effective_preset(table, [standard], rules=())
    assert effective is None


def test_effective_preset_skips_default_for_ephemeral_tables() -> None:
    # The default-preset fallback must not sweep up temp/scratch tables, same exclusion the
    # rule-match path already applies ().
    default_native = PresetConfig(
        "default-native", "default-native-deep", "native", priority=0, is_default=True
    )
    table = TableCandidate("table-1", "temp", "scratch_orders", {"total_size_gb": 5})
    effective = effective_preset(table, [default_native], rules=())
    assert effective is None


def test_effective_preset_prefers_rule_over_unassigned() -> None:
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    large = PresetConfig("large", "large", "spark", priority=200)
    manual = PresetConfig("manual", "manual", "trino", priority=50)
    # An is_default preset exists and is used as a fallback only when nothing else matches --
    # rule and assignment provenance must still win over it.
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        priority=0,
        is_default=True,
    )
    table = TableCandidate(
        "table-1",
        "mview",
        "events",
        {"total_size_gb": 300, "small_files_count": 1_000},
    )
    rules = [
        PresetRule(
            "small-files", "standard", {"field": "small_files_count", "op": ">", "value": 10}
        ),
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(table, [standard, large, manual, default_native], rules)
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule"

    assigned = effective_preset(
        table,
        [standard, large, manual, default_native],
        rules,
        [TableAssignment("table-1", "manual")],
    )
    assert assigned is not None
    assert assigned.preset.id == "manual"
    assert assigned.provenance == "assignment"

    # No rules, no assignment -> falls back to the is_default preset.
    unassigned = effective_preset(table, [standard, large, manual, default_native], rules=())
    assert unassigned is not None
    assert unassigned.preset.id == "default-native"
    assert unassigned.provenance == "default"


def test_effective_preset_falls_through_to_rules_when_pinned_preset_is_disabled() -> None:
    # A table pinned to a preset that has since been disabled (retired, replaced) must
    # not be blackholed forever -- it should fall through to the normal rule engine.
    # Found live in production: 21 tables pinned to disabled presets, including a 24TB
    # table sitting with needs_compaction=True, never rescheduled because the pin
    # short-circuited before the rule loop ever ran.
    stale_preset = PresetConfig("stale", "retired-preset", "spark", priority=999, enabled=False)
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 300})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [stale_preset, large],
        rules,
        [TableAssignment("table-1", "stale")],
    )
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule_after_stale_pin"


def test_effective_preset_falls_through_to_rules_when_pinned_preset_does_not_exist() -> None:
    # Same fallback behavior when the pinned preset id references a preset that was
    # deleted outright, not just disabled.
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 300})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [large],
        rules,
        [TableAssignment("table-1", "deleted-preset-id")],
    )
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule_after_stale_pin"


def test_effective_preset_still_none_when_stale_pin_and_no_rule_matches() -> None:
    # The fallback only helps when a rule actually matches -- a stale pin with no
    # matching rule still resolves to None, same as the no-assignment-no-rule case.
    stale_preset = PresetConfig("stale", "retired-preset", "spark", priority=999, enabled=False)
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 5})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [stale_preset, large],
        rules,
        [TableAssignment("table-1", "stale")],
    )
    assert effective is None


def test_tables_for_preset_uses_effective_preset_and_skips_inactive_tables() -> None:
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    large = PresetConfig("large", "large", "spark", priority=200)
    tables = [
        TableCandidate("table-1", "mview", "events", {"total_size_gb": 300}),
        TableCandidate("table-2", "raw", "clicks", {"total_size_gb": 20}),
        TableCandidate("table-3", "raw", "old", {"total_size_gb": 500}, is_active=False),
    ]
    rules = [
        PresetRule("default", "standard", {"field": "total_size_gb", "op": ">=", "value": 0}),
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    assert [table.id for table in tables_for_preset(large, tables, [standard, large], rules)] == [
        "table-1"
    ]
    assert [
        table.id for table in tables_for_preset(standard, tables, [standard, large], rules)
    ] == ["table-2"]


def test_tables_for_preset_excludes_tables_with_a_future_missing_metadata_retry_after() -> None:
    """Critical vitals action plan, 2026-09-20: a table under missing-metadata containment
    (record_missing_metadata) must not be re-selected for scheduling until its retry-after
    passes -- same shape as the is_active gate, but reversible and bounded."""
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    now = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    tables = [
        TableCandidate("table-1", "s", "healthy", {"total_size_gb": 10}),
        TableCandidate(
            "table-2",
            "s",
            "contained",
            {"total_size_gb": 10},
            maintenance_retry_after=now + timedelta(hours=1),
        ),
        TableCandidate(
            "table-3",
            "s",
            "retry_window_passed",
            {"total_size_gb": 10},
            maintenance_retry_after=now - timedelta(minutes=1),
        ),
    ]
    rules = [PresetRule("all", "standard", {"field": "total_size_gb", "op": ">=", "value": 0})]

    matched = tables_for_preset(standard, tables, [standard], rules, now=now)

    assert [t.id for t in matched] == ["table-1", "table-3"]


def test_tables_for_preset_treats_a_naive_retry_after_as_utc() -> None:
    """SQLite (the test backend) drops tzinfo on read-back -- the gate must still work
    correctly on a naive datetime, same pattern as run_finalizer.py's grace-window check."""
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    now = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    naive_future = datetime(2026, 9, 20, 18, 0)  # no tzinfo
    tables = [
        TableCandidate(
            "table-1", "s", "contained", {"total_size_gb": 10}, maintenance_retry_after=naive_future
        ),
    ]
    rules = [PresetRule("all", "standard", {"field": "total_size_gb", "op": ">=", "value": 0})]

    matched = tables_for_preset(standard, tables, [standard], rules, now=now)

    assert matched == []


def test_ephemeral_temp_tables_are_not_rule_matched_but_pins_still_win() -> None:
    # : a fleet-wide rule used to pick up `_temp` / schema `temp` scratch tables
    # and burn Spark-on-K8s drivers. Code-level skip so new presets cannot reintroduce them.
    standard = PresetConfig("standard", "standard", "spark", priority=100)
    catch_all = PresetRule("all", "standard", {"field": "total_size_gb", "op": ">=", "value": 0})
    temp_table = TableCandidate(
        "t-temp",
        "revenue",
        "silver_infinite_practice_result_questions_temp",
        {"total_size_gb": 4000},
    )
    schema_temp = TableCandidate(
        "t-schema", "temp", "signup_login_funnel_2026", {"total_size_gb": 1}
    )
    live = TableCandidate("t-live", "mview", "events", {"total_size_gb": 10})

    assert effective_preset(temp_table, [standard], [catch_all]) is None
    assert effective_preset(schema_temp, [standard], [catch_all]) is None
    matched = effective_preset(live, [standard], [catch_all])
    assert matched is not None and matched.preset.id == "standard"

    pinned = effective_preset(
        temp_table,
        [standard],
        [catch_all],
        [TableAssignment("t-temp", "standard")],
    )
    assert pinned is not None and pinned.provenance == "assignment"


def test_ephemeral_skip_carves_out_testpress_and_ignores_test_in_the_middle() -> None:
    standard = PresetConfig("standard", "standard", "spark", priority=100)
    catch_all = PresetRule("all", "standard", {"field": "total_size_gb", "op": ">=", "value": 0})
    testpress = TableCandidate("t-tp", "testpress", "attempts", {"total_size_gb": 50})
    gold_test = TableCandidate(
        "t-gold", "user_experience", "gold_test_student_mappings", {"total_size_gb": 20}
    )
    suffix_test = TableCandidate(
        "t-suffix", "reports", "gold_dbt_video_stats_reports_unified_test", {"total_size_gb": 5}
    )

    assert effective_preset(testpress, [standard], [catch_all]) is not None
    assert effective_preset(gold_test, [standard], [catch_all]) is not None
    assert effective_preset(suffix_test, [standard], [catch_all]) is None


def test_tables_for_preset_matches_unassigned_tables_via_default_fallback() -> None:
    # Without rules or assignments, tables now match the is_default preset (2026-09-29).
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        priority=0,
        is_default=True,
    )
    tables = [
        TableCandidate("table-1", "demo", "orders", {"total_size_gb": 5}),
        TableCandidate("table-2", "demo", "events", {"total_size_gb": 1}),
    ]
    matched = tables_for_preset(default_native, tables, [default_native], rules=())
    assert {table.id for table in matched} == {"table-1", "table-2"}
