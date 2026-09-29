from cryo.maintenance.funnel import decision_funnel


def test_funnel_does_not_invent_stages_the_summary_lacks() -> None:
    summary = {
        "pipeline": "batch",
        "selected": 4,
        "succeeded": 3,
        "failed": 1,
        "over_budget": 2,
    }
    stages = {row["stage"]: row for row in decision_funnel(summary)}
    assert stages["needs_work"]["count"] is None
    assert stages["eligible"]["reason"] == "not recorded"
    assert stages["selected"]["count"] == 4
    assert stages["ran"]["count"] == 4
    assert stages["succeeded"]["count"] == 3


def test_funnel_is_unrecorded_without_a_batch_summary() -> None:
    assert all(row["count"] is None for row in decision_funnel(None))
