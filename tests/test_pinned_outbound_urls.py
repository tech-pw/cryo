import pytest

from cryo.security.outbound import (
    OutboundURLError,
    azure_openai_url,
    openai_chat_url,
    slack_webhook_url,
)


def test_slack_webhook_is_rebuilt_from_a_fixed_host() -> None:
    url = slack_webhook_url("https://hooks.slack.com/services/T/B/secret")
    assert url == "https://hooks.slack.com/services/T/B/secret"
    with pytest.raises(OutboundURLError):
        slack_webhook_url("https://evil.example/services/T/B/secret")
    with pytest.raises(OutboundURLError):
        slack_webhook_url("https://hooks.slack.com/latest/meta-data")
    with pytest.raises(OutboundURLError):
        slack_webhook_url("http://hooks.slack.com/services/T/B/secret")


def test_openai_url_keeps_only_known_hosts() -> None:
    assert (
        openai_chat_url("https://api.openai.com/v1/chat/completions")
        == "https://api.openai.com/v1/chat/completions"
    )
    assert (
        openai_chat_url(
            "https://my-resource.openai.azure.com/openai/deployments/gpt/chat/completions"
        )
        == "https://my-resource.openai.azure.com/openai/deployments/gpt/chat/completions"
    )
    with pytest.raises(OutboundURLError):
        openai_chat_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(OutboundURLError):
        openai_chat_url("https://llm.internal.example/v1/chat/completions")


def test_azure_url_allows_apim_suffix() -> None:
    url = azure_openai_url(
        "https://contoso.azure-api.net/openai/deployments/gpt/chat/completions?api-version=2024-03-01-preview"
    )
    assert url.startswith("https://contoso.azure-api.net/openai/deployments/gpt/chat/completions?")
    with pytest.raises(OutboundURLError):
        azure_openai_url("https://contoso.example/openai/deployments/gpt/chat/completions")
