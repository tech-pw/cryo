from __future__ import annotations

import json

from cryo.telemetry.iceberg_source import (
    _accumulate,
    _accumulate_ranges,
    _selectivity_for_query,
    parse_iceberg_query_log_config,
)
from cryo.telemetry.parser import parse_query_predicates
from cryo.telemetry.patterns import ColumnPatternStats


class TestSelectivityForQuery:
    """Unit coverage for extracting real per-query selectivity out of Trino's inputs_json --
    the 2026-08-14 finding was that query_patterns.avg_selectivity was NULL for all 22,671
    rows because nothing computed it, silently forcing every layout/partition recommendation
    onto PtoPattern.pruning_value's 0.5 "no information" fallback."""

    def test_extracts_real_selectivity_for_matched_table(self) -> None:
        inputs_json = json.dumps(
            [
                {
                    "schema": "pw_experimentation",
                    "table": "gold_unleash_experiments_v1",
                    "physicalInputRows": 181547999,
                    "connectorInfo": {"totalRecords": "76524592123"},
                }
            ]
        )
        result = _selectivity_for_query(
            inputs_json,
            schema_name="pw_experimentation",
            table_name="gold_unleash_experiments_v1",
        )
        assert result == 181547999 / 76524592123

    def test_matches_by_schema_and_table_not_position(self) -> None:
        # A join query -- the entry for a metadata/system table sits first, real target second.
        inputs_json = json.dumps(
            [
                {
                    "schema": "information_schema",
                    "table": "tables",
                    "physicalInputRows": 19,
                    "connectorInfo": {"totalRecords": "19"},
                },
                {
                    "schema": "pw_bq",
                    "table": "silver_dbt_enter_name",
                    "physicalInputRows": 500,
                    "connectorInfo": {"totalRecords": "5000"},
                },
            ]
        )
        result = _selectivity_for_query(
            inputs_json, schema_name="pw_bq", table_name="silver_dbt_enter_name"
        )
        assert result == 0.1

    def test_returns_none_when_table_not_present_in_inputs(self) -> None:
        inputs_json = json.dumps(
            [
                {
                    "schema": "other_schema",
                    "table": "other_table",
                    "physicalInputRows": 10,
                    "connectorInfo": {"totalRecords": "100"},
                }
            ]
        )
        result = _selectivity_for_query(
            inputs_json, schema_name="pw_bq", table_name="silver_dbt_enter_name"
        )
        assert result is None

    def test_returns_none_for_missing_inputs_json(self) -> None:
        assert _selectivity_for_query(None, schema_name="s", table_name="t") is None
        assert _selectivity_for_query("", schema_name="s", table_name="t") is None

    def test_returns_none_for_unparseable_json(self) -> None:
        assert _selectivity_for_query("not-json{{{", schema_name="s", table_name="t") is None

    def test_returns_none_when_connector_metrics_missing_totalrecords(self) -> None:
        # e.g. a non-Iceberg connector, or connectorInfo present but sparse -- must not crash
        # or silently invent a ratio.
        inputs_json = json.dumps(
            [
                {
                    "schema": "s",
                    "table": "t",
                    "physicalInputRows": 10,
                    "connectorInfo": {},
                }
            ]
        )
        assert _selectivity_for_query(inputs_json, schema_name="s", table_name="t") is None

    def test_returns_none_when_total_records_is_zero(self) -> None:
        # Guards the division -- a table reporting 0 total rows makes the ratio meaningless,
        # not "perfect selectivity".
        inputs_json = json.dumps(
            [
                {
                    "schema": "s",
                    "table": "t",
                    "physicalInputRows": 0,
                    "connectorInfo": {"totalRecords": "0"},
                }
            ]
        )
        assert _selectivity_for_query(inputs_json, schema_name="s", table_name="t") is None

    def test_returns_none_when_connector_info_is_not_a_dict(self) -> None:
        inputs_json = json.dumps(
            [{"schema": "s", "table": "t", "physicalInputRows": 10, "connectorInfo": None}]
        )
        assert _selectivity_for_query(inputs_json, schema_name="s", table_name="t") is None

    def test_returns_none_when_top_level_json_is_not_a_list(self) -> None:
        assert (
            _selectivity_for_query(json.dumps({"not": "a list"}), schema_name="s", table_name="t")
            is None
        )


class TestAccumulateWithSelectivity:
    """_accumulate is the shared fold-in step aggregate_from_iceberg_query_log calls once per
    matched query; selectivity should land on filter columns only."""

    def test_selectivity_is_recorded_on_filter_columns(self) -> None:
        predicate_info = parse_query_predicates(
            "SELECT * FROM t WHERE event_date > DATE '2026-01-01'"
        )
        stats_by_col: dict[str, ColumnPatternStats] = {}

        _accumulate(stats_by_col, predicate_info, selectivity=0.15)

        assert stats_by_col["event_date"].avg_selectivity == 0.15

    def test_selectivity_none_leaves_avg_selectivity_none(self) -> None:
        predicate_info = parse_query_predicates(
            "SELECT * FROM t WHERE event_date = DATE '2026-01-01'"
        )
        stats_by_col: dict[str, ColumnPatternStats] = {}

        _accumulate(stats_by_col, predicate_info, selectivity=None)

        assert stats_by_col["event_date"].avg_selectivity is None

    def test_join_and_projection_columns_are_unaffected_by_selectivity(self) -> None:
        predicate_info = parse_query_predicates(
            "SELECT amount FROM t JOIN u ON t.customer_id = u.id "
            "WHERE event_date = DATE '2026-01-01'"
        )
        stats_by_col: dict[str, ColumnPatternStats] = {}

        _accumulate(stats_by_col, predicate_info, selectivity=0.4)

        assert stats_by_col["event_date"].avg_selectivity == 0.4
        # customer_id/amount are join/projection-only columns for this query -- selectivity is
        # a filter-pruning outcome and shouldn't be attributed to columns the predicate never
        # touched.
        if "customer_id" in stats_by_col:
            assert stats_by_col["customer_id"].avg_selectivity is None
        if "amount" in stats_by_col:
            assert stats_by_col["amount"].avg_selectivity is None

    def test_repeated_calls_average_selectivity_across_queries(self) -> None:
        predicate_info = parse_query_predicates(
            "SELECT * FROM t WHERE event_date = DATE '2026-01-01'"
        )
        stats_by_col: dict[str, ColumnPatternStats] = {}

        _accumulate(stats_by_col, predicate_info, selectivity=0.2)
        _accumulate(stats_by_col, predicate_info, selectivity=0.6)

        assert stats_by_col["event_date"].avg_selectivity == 0.4
        assert stats_by_col["event_date"].filter_count == 2


class TestParseIcebergQueryLogConfig:
    def test_defaults_inputs_json_column_to_inputs_json(self) -> None:
        config = parse_iceberg_query_log_config(
            {"query_log": {"mode": "iceberg_table", "table": "logging.trino_queries"}}
        )
        assert config is not None
        assert config.inputs_json_column == "inputs_json"

    def test_explicit_inputs_json_column_is_honored(self) -> None:
        config = parse_iceberg_query_log_config(
            {
                "query_log": {
                    "mode": "iceberg_table",
                    "table": "logging.trino_queries",
                    "inputs_json_column": "custom_inputs",
                }
            }
        )
        assert config is not None
        assert config.inputs_json_column == "custom_inputs"

    def test_null_inputs_json_column_disables_selectivity_extraction(self) -> None:
        config = parse_iceberg_query_log_config(
            {
                "query_log": {
                    "mode": "iceberg_table",
                    "table": "logging.trino_queries",
                    "inputs_json_column": None,
                }
            }
        )
        assert config is not None
        assert config.inputs_json_column is None

    def test_false_inputs_json_column_disables_selectivity_extraction(self) -> None:
        config = parse_iceberg_query_log_config(
            {
                "query_log": {
                    "mode": "iceberg_table",
                    "table": "logging.trino_queries",
                    "inputs_json_column": False,
                }
            }
        )
        assert config is not None
        assert config.inputs_json_column is None

    def test_cost_columns_default_to_trino_event_listener_names(self) -> None:
        config = parse_iceberg_query_log_config(
            {"query_log": {"mode": "iceberg_table", "table": "logging.trino_queries"}}
        )
        assert config is not None
        assert config.cpu_time_column == "cpu_time_millis"
        assert config.input_bytes_column == "physical_input_bytes"


class TestAccumulateRanges:
    def test_counts_numeric_and_temporal_ranges_with_their_cost(self) -> None:
        hits: dict = {}
        sql = (
            "SELECT * FROM t WHERE event_date >= DATE '2026-09-01' "
            "AND event_date < DATE '2026-09-08' AND amount > 10"
        )

        _accumulate_ranges(hits, sql, cpu_ms=400, input_bytes=2000)
        _accumulate_ranges(hits, sql, cpu_ms=None, input_bytes=None)

        assert hits[("event_date", "2026-09-01", "2026-09-08")] == {
            "queries": 2,
            "cpu_ms": 400.0,
            "input_bytes": 2000,
        }
        assert hits[("amount", 10, None)]["queries"] == 2

    def test_text_literals_are_never_kept(self) -> None:
        hits: dict = {}

        _accumulate_ranges(hits, "SELECT * FROM t WHERE email BETWEEN 'a@x.com' AND 'b@x.com'")

        assert hits == {}
