from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from trino.auth import BasicAuthentication

import trino


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


def coordinators_from_config(config: Mapping[str, Any]) -> tuple[TrinoCoordinator, ...]:
    raw_coordinators = config.get("coordinators") or config.get("hosts") or config.get("host")
    if raw_coordinators is None:
        raw_coordinators = ["localhost"]
    if isinstance(raw_coordinators, str):
        raw_coordinators = [raw_coordinators]

    return tuple(_coordinator_from_raw(item, config) for item in raw_coordinators)


def connect(coordinator: TrinoCoordinator) -> Any:
    return trino.dbapi.connect(
        host=coordinator.host,
        port=coordinator.port,
        user=coordinator.user,
        catalog=coordinator.catalog,
        schema=coordinator.schema,
        http_scheme=coordinator.http_scheme,
        auth=coordinator.auth,
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
