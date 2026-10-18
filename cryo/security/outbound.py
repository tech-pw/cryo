"""Build outbound URLs from a fixed host so a saved base URL cannot retarget the request."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_QUERY = re.compile(r"[A-Za-z0-9=&._-]{1,200}")


class OutboundURLError(ValueError):
    pass


def slack_webhook_url(url: str) -> str:
    parsed = _https(url)
    path = _path(parsed.path, prefix="/services/")
    if parsed.query or parsed.fragment:
        raise OutboundURLError("Slack webhook URL must not include a query or fragment")
    host = _host(parsed)
    if host == "hooks.slack.com":
        return "https://hooks.slack.com" + path
    if host == "hooks.slack-gov.com":
        return "https://hooks.slack-gov.com" + path
    raise OutboundURLError("Slack webhook host is not allowed")


def openai_chat_url(url: str) -> str:
    parsed = _https(url)
    path = _path(parsed.path or "/v1/chat/completions")
    suffix = _with_query(path, parsed.query)
    host = _host(parsed)
    if host == "api.openai.com":
        return "https://api.openai.com" + suffix
    label = _suffix_label(host, ".openai.azure.com")
    if label is not None:
        return f"https://{label}.openai.azure.com{suffix}"
    raise OutboundURLError("OpenAI API host is not allowed")


def azure_openai_url(url: str) -> str:
    parsed = _https(url)
    path = _path(parsed.path or "/")
    suffix = _with_query(path, parsed.query)
    host = _host(parsed)
    for suffix_host in (".openai.azure.com", ".azure-api.net"):
        label = _suffix_label(host, suffix_host)
        if label is not None:
            if suffix_host == ".openai.azure.com":
                return f"https://{label}.openai.azure.com{suffix}"
            return f"https://{label}.azure-api.net{suffix}"
    raise OutboundURLError("Azure OpenAI API host is not allowed")


def _https(url: str):
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise OutboundURLError(
            "outbound URL must be https on port 443 and must not embed credentials"
        )
    if not parsed.hostname:
        raise OutboundURLError("outbound URL host is not allowed")
    return parsed


def _host(parsed) -> str:
    return parsed.hostname.lower().rstrip(".")


def _path(path: str, *, prefix: str = "/") -> str:
    if not path.startswith(prefix) or "\\" in path or any(part == ".." for part in path.split("/")):
        raise OutboundURLError("outbound URL path is not allowed")
    return path


def _with_query(path: str, query: str) -> str:
    if not query:
        return path
    if not _QUERY.fullmatch(query):
        raise OutboundURLError("outbound URL query is not allowed")
    return f"{path}?{query}"


def _suffix_label(host: str, suffix: str) -> str | None:
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not _LABEL.fullmatch(label):
        return None
    return label
