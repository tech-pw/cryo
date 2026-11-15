"""Single-use tickets that authenticate the run console WebSocket (#551).

Browsers cannot set an ``Authorization`` header on a WebSocket, and a bearer token in the URL
leaks into proxy/ingress access logs, browser history and Referer. The client instead trades its
bearer token for an opaque ticket over a normal authenticated POST, then opens the socket with
``?ticket=``. The ticket is bound to the principal and one run, lives ~30 s, is stored only as a
SHA-256 hash, and is consumed atomically (GETDEL) on connect, so a ticket that shows up in a log
is already spent.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable
from typing import Any

from cryo.api.security import Principal, Role, parse_role

TICKET_TTL_SECONDS = 30
_MAX_TICKET_LEN = 256
_KEY_PREFIX = "cryo:ws_ticket:"


class TicketStoreUnavailableError(RuntimeError):
    pass


def _key(ticket: str) -> str:
    return _KEY_PREFIX + hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def _default_redis() -> Any:
    from cryo.queue.redis_queue import get_redis_client

    return get_redis_client()


class WsTicketStore:
    def __init__(self, redis_getter: Callable[[], Any] = _default_redis) -> None:
        self.redis_getter = redis_getter

    def _client(self) -> Any:
        try:
            client = self.redis_getter()
        except Exception as exc:  # noqa: BLE001
            raise TicketStoreUnavailableError("redis unavailable") from exc
        if client is None:
            raise TicketStoreUnavailableError("redis not configured")
        return client

    def issue(self, principal: Principal, run_id: uuid.UUID) -> str:
        ticket = secrets.token_urlsafe(32)
        record = {
            "subject": principal.subject,
            "roles": sorted(role.value for role in principal.roles),
            "tenant_id": str(principal.tenant_id) if principal.tenant_id else None,
            "run_id": str(run_id),
        }
        client = self._client()
        try:
            client.set(_key(ticket), json.dumps(record), ex=TICKET_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            raise TicketStoreUnavailableError("redis write failed") from exc
        return ticket

    def consume(self, ticket: str, run_id: uuid.UUID) -> tuple[Principal | None, str]:
        """Return ``(principal, "ok")`` or ``(None, reason)``. Found tickets are spent."""
        if not ticket or len(ticket) > _MAX_TICKET_LEN:
            return None, "invalid_ticket"
        try:
            raw = self._client().getdel(_key(ticket))
        except Exception:  # noqa: BLE001
            return None, "ticket_store_unavailable"
        if raw is None:
            return None, "invalid_ticket"
        try:
            record = json.loads(raw)
            ticket_run = uuid.UUID(str(record["run_id"]))
            roles: set[Role] = set()
            for value in record["roles"]:
                role = parse_role(str(value))
                if role is not None:
                    roles.add(role)
            tenant_raw = record.get("tenant_id")
            principal = Principal(
                subject=str(record["subject"]),
                roles=frozenset(roles),
                tenant_id=uuid.UUID(str(tenant_raw)) if tenant_raw else None,
            )
        except (ValueError, KeyError, TypeError):
            return None, "invalid_ticket"
        if ticket_run != run_id:
            return None, "run_mismatch"
        return principal, "ok"


ws_ticket_store = WsTicketStore()
