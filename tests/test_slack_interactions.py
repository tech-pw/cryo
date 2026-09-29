from cryo.api.slack_routes import slack_interaction_response


def test_slack_interaction_does_not_run_maintenance() -> None:
    response = slack_interaction_response(
        {"actions": [{"action_id": "diagnose", "value": "11111111-1111-1111-1111-111111111111"}]}
    )
    assert response["response_type"] == "ephemeral"
    assert (
        "run maintenance" not in response["text"].lower()
        or "does not run maintenance" in response["text"].lower()
    )
    assert response["text"].startswith("Open this run in Cryo:")


def test_slack_interaction_without_a_run_refuses_to_act() -> None:
    response = slack_interaction_response({"actions": [{"action_id": "approve"}]})
    assert "does not run maintenance" in response["text"]
