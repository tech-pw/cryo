"""Verify a Google Identity Services ID token. Client secret is not required."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo?id_token="
_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_google_id_token(
    id_token: str,
    *,
    client_id: str,
    hosted_domain: str | None = None,
    fetcher: Any = None,
) -> dict[str, str] | None:
    fetch = fetcher or _fetch_tokeninfo
    try:
        claims = fetch(id_token)
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    if claims.get("aud") != client_id:
        return None
    if claims.get("iss") not in _ISSUERS:
        return None
    if str(claims.get("email_verified")).lower() not in {"true", "1"}:
        return None
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        return None
    domain = (hosted_domain or "").strip().lower()
    if domain and not email.lower().endswith(f"@{domain}"):
        return None
    return {"email": email.lower(), "subject": str(claims.get("sub") or email.lower())}


def _fetch_tokeninfo(id_token: str) -> dict[str, Any]:
    with urlopen(f"{_TOKENINFO}{quote(id_token, safe='')}", timeout=8) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("tokeninfo was not an object")
    return payload
