from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from trino.auth import BasicAuthentication

import trino
from cryo.config import get_settings


@dataclass(frozen=True)
class TrinoCoordinator:
    host: str
    port: int = 8080
    http_scheme: str = "http"
    user: str = "cryo"
    catalog: str = "iceberg"
    schema: str | None = None
    auth: BasicAuthentication | None = None

    @property
    def label(self) -> str:
        return f"{self.http_scheme}://{self.host}:{self.port}"


def _connect_timeout() -> float:
    """Read the configured connect timeout, lazily."""
    try:
        return float(get_settings().engine_connect_timeout_sec)
    except Exception:
        return 5.0


def coordinators_from_config(config: Mapping[str, Any]) -> tuple[TrinoCoordinator, ...]:
    raw_coordinators = config.get("coordinators") or config.get("hosts") or config.get("host")
    if raw_coordinators is None:
        raw_coordinators = ["localhost"]
    if isinstance(raw_coordinators, str):
        raw_coordinators = [raw_coordinators]

    return tuple(_coordinator_from_raw(item, config) for item in raw_coordinators)


def connect(coordinator: TrinoCoordinator) -> Any:
    """Connect to a Trino coordinator with a fail-fast connect timeout.

    ``trino.dbapi.connect`` forwards ``request_timeout`` to the underlying
    HTTP client as ``(connect_timeout, read_timeout)``. Without it the urllib3
    default of 60s hangs the worker while waiting on unreachable hosts.
    ``max_attempts`` defaults to 3, which makes initial probe failures take
    3x the connect timeout; we cap at 1 so the caller surfaces the failure
    immediately and the governor can route around the dead coordinator.
    """
    timeout = _connect_timeout()
    return trino.dbapi.connect(
        host=coordinator.host,
        port=coordinator.port,
        user=coordinator.user,
        catalog=coordinator.catalog,
        schema=coordinator.schema,
        http_scheme=coordinator.http_scheme,
        auth=coordinator.auth,
        request_timeout=timeout,
        max_attempts=1,
    )


def _coordinator_from_raw(raw: object, defaults: Mapping[str, Any]) -> TrinoCoordinator:
    config = dict(defaults)
    if isinstance(raw, Mapping):
        config.update(raw)
    else:
        config["host"] = str(raw)

    auth = _auth_from_config(config)
    return TrinoCoordinator(
        host=str(config.get("host") or config.get("coordinator") or "localhost"),
        port=int(config.get("port") or 8080),
        http_scheme=str(config.get("http_scheme") or config.get("scheme") or "http"),
        user=str(config.get("user") or config.get("username") or "cryo"),
        catalog=str(config.get("catalog") or "iceberg"),
        schema=str(config["schema"]) if config.get("schema") else None,
        auth=auth,
    )


def _auth_from_config(config: Mapping[str, Any]) -> BasicAuthentication | None:
    auth_config = config.get("auth")
    if isinstance(auth_config, Mapping):
        auth_type = str(auth_config.get("type") or "").lower()
        password = auth_config.get("password")
        username = auth_config.get("username") or config.get("user") or config.get("username")
        if auth_type == "basic" and username and password:
            return BasicAuthentication(str(username), str(password))

    password = config.get("password")
    if password:
        return BasicAuthentication(str(config.get("user") or "cryo"), str(password))
    return None
