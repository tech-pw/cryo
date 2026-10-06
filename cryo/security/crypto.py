"""Encrypt sensitive values at rest using a Fernet key derived from CRYO_SECRET_KEY."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from cryo.config import get_settings

_PREFIX = "enc:"


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    if plaintext.startswith(_PREFIX):
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(stored: str | None) -> str | None:
    if stored is None or stored == "":
        return None
    if not stored.startswith(_PREFIX):
        # Legacy plaintext at rest — still readable until next write re-encrypts.
        return stored
    try:
        return _fernet().decrypt(stored.removeprefix(_PREFIX).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt stored secret") from exc
