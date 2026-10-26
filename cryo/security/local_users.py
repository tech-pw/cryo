"""Current role and status of Cryo-managed (password or Google) users, for token checks.

Tokens issued by /auth/login and /auth/google carry ``auth="local"`` and the user's id.
Resolving the role from the database on use, rather than trusting the role baked into the
token, makes a role change or a disable take effect within the cache TTL instead of at the
token's 24-hour expiry.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.exc import SQLAlchemyError

from cryo.db.session import SessionLocal

LOCAL_AUTH: Final = "local"
CACHE_TTL_SECONDS: Final = 15.0


@dataclass(frozen=True, slots=True)
class LocalUserState:
    username: str
    role: str
    disabled: bool


class _Unavailable:
    pass


UNAVAILABLE: Final = _Unavailable()

_cache: dict[str, tuple[float, LocalUserState | None]] = {}
_lock = threading.Lock()


def lookup_local_user(user_id: str) -> LocalUserState | None | _Unavailable:
    """The user's current state, None if the user no longer exists, or UNAVAILABLE when the
    database cannot be read (callers fall back to the token's own claims)."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(user_id)
        if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    try:
        key = uuid.UUID(user_id)
    except ValueError:
        return None
    from cryo.db.models import LocalUser

    try:
        with SessionLocal() as session:
            row = session.get(LocalUser, key)
            state = (
                LocalUserState(username=row.username, role=row.role, disabled=row.disabled)
                if row is not None
                else None
            )
    except SQLAlchemyError:
        return UNAVAILABLE
    with _lock:
        _cache[user_id] = (now, state)
    return state


def invalidate_local_user(user_id: uuid.UUID | str | None = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(str(user_id), None)
