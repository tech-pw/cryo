"""Net-savings arithmetic: storage saved minus rewrite, maintenance, and platform fees."""

from __future__ import annotations

import pytest

from cryo.finops.net_savings import (
    MaintenanceScenario,
    compare_scenarios,
    evaluate_scenario,
)

_TB = 1024**4


def _self_hosted(saved: float = 0.20) -> MaintenanceScenario:
    return MaintenanceScenario("self-hosted", saved, rewrite_cost_per_gb=0.004)


def test_gross_savings_scale_with_size_rate_and_horizon() -> None:
    result = evaluate_scenario(
        MaintenanceScenario("free rewrite", 0.5),
        table_size_bytes=1024 * _TB,  # 1024 TiB == 1,048,576 GiB
        storage_cost_per_gb_month=0.023,
        horizon_days=30,
    )
    # Half of 1,048,576 GiB saved for one month at $0.023/GiB-month.
    assert result.gross_storage_savings_usd == pytest.approx(524288 * 0.023, rel=1e-6)
    assert result.net_savings_usd == pytest.approx(result.gross_storage_savings_usd)


def test_platform_fee_reduces_what_the_customer_keeps() -> None:
    charged = evaluate_scenario(
        MaintenanceScenario("vendor", 0.30, platform_fee_fraction_of_savings=0.25),
        table_size_bytes=100 * _TB,
    )
    assert charged.platform_fee_usd == pytest.approx(charged.gross_storage_savings_usd * 0.25)
    assert charged.customer_keeps_fraction == pytest.approx(0.75)


def test_higher_gross_reduction_can_still_lose_on_net() -> None:
    """The central comparison: bytes saved is not the number that matters.

    A vendor saving substantially more bytes can return less to the customer once its fee and
    its own compute are charged. Cryo does not need to win on compression to win here.
    """
    comparison = compare_scenarios(
        [
            MaintenanceScenario(
                "vendor",
                0.30,
                rewrite_cost_per_gb=0.02,
                platform_fee_fraction_of_savings=0.25,
                monthly_maintenance_fraction=0.05,
            ),
            MaintenanceScenario(
                "self-hosted",
                0.18,
                rewrite_cost_per_gb=0.004,
                monthly_maintenance_fraction=0.05,
            ),
        ],
        table_size_bytes=500 * _TB,
    )
    best = comparison.best
    assert best is not None
    assert best.name == "self-hosted"
    vendor = next(r for r in comparison.results if r.name == "vendor")
    assert vendor.storage_saved_bytes > best.storage_saved_bytes  # vendor saves more bytes
    assert vendor.net_savings_usd < best.net_savings_usd  # and still returns less


def test_recurring_fee_grows_against_one_time_rewrite_over_time() -> None:
    """A percentage-of-savings fee recurs; the layout fix that earned it is mostly one-time."""
    vendor = MaintenanceScenario("vendor", 0.30, platform_fee_fraction_of_savings=0.25)
    short = evaluate_scenario(vendor, table_size_bytes=100 * _TB, horizon_days=90)
    long = evaluate_scenario(vendor, table_size_bytes=100 * _TB, horizon_days=1095)
    assert long.platform_fee_usd > short.platform_fee_usd * 10


def test_rewrite_cost_is_charged_once_not_per_month() -> None:
    scenario = MaintenanceScenario("one-off", 0.2, rewrite_cost_per_gb=0.01)
    short = evaluate_scenario(scenario, table_size_bytes=10 * _TB, horizon_days=30)
    long = evaluate_scenario(scenario, table_size_bytes=10 * _TB, horizon_days=365)
    assert short.rewrite_cost_usd == pytest.approx(long.rewrite_cost_usd)


def test_maintenance_cost_accrues_with_the_horizon() -> None:
    scenario = MaintenanceScenario(
        "churny", 0.2, rewrite_cost_per_gb=0.01, monthly_maintenance_fraction=0.10
    )
    short = evaluate_scenario(scenario, table_size_bytes=10 * _TB, horizon_days=30)
    long = evaluate_scenario(scenario, table_size_bytes=10 * _TB, horizon_days=360)
    assert long.maintenance_cost_usd == pytest.approx(short.maintenance_cost_usd * 12, rel=1e-6)


def test_expensive_rewrite_can_make_net_negative() -> None:
    result = evaluate_scenario(
        MaintenanceScenario("not worth it", 0.05, rewrite_cost_per_gb=1.0),
        table_size_bytes=10 * _TB,
        horizon_days=30,
    )
    assert result.net_savings_usd < 0
    assert result.customer_keeps_fraction < 0


def test_payback_is_none_when_recurring_costs_exceed_savings() -> None:
    result = evaluate_scenario(
        MaintenanceScenario(
            "underwater",
            0.05,
            rewrite_cost_per_gb=0.01,
            platform_fee_fraction_of_savings=1.0,
            monthly_maintenance_fraction=0.5,
        ),
        table_size_bytes=10 * _TB,
    )
    assert result.payback_days is None


def test_saved_fraction_is_clamped_to_a_sane_range() -> None:
    over = evaluate_scenario(MaintenanceScenario("absurd", 5.0), table_size_bytes=_TB)
    assert over.storage_saved_bytes == _TB
    under = evaluate_scenario(MaintenanceScenario("negative", -1.0), table_size_bytes=_TB)
    assert under.storage_saved_bytes == 0


def test_assumptions_are_recorded_for_audit() -> None:
    """Every input must be inspectable so a projection can be disputed."""
    result = evaluate_scenario(_self_hosted(), table_size_bytes=_TB)
    for key in (
        "table_size_bytes",
        "storage_cost_per_gb_month",
        "storage_saved_fraction",
        "rewrite_cost_per_gb",
        "platform_fee_fraction_of_savings",
        "horizon_days",
    ):
        assert key in result.assumptions
