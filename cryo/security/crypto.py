"""Encrypt sensitive values at rest using a Fernet key derived from CRYO_SECRET_KEY."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from cryo.config import get_settings

_PREFIX = "enc:"


def fernet_for(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> MultiFernet:
    """Encrypts with secret_key; decrypts with secret_key, then secret_key_previous."""
    settings = get_settings()
    keys = [fernet_for(settings.secret_key)]
    previous = settings.secret_key_previous
    if previous and previous != settings.secret_key:
        keys.append(fernet_for(previous))
    return MultiFernet(keys)


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
