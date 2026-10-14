"""Direct Hive Metastore Thrift client for warehouse locations.

Queries ``StorageDescriptor.location`` over Thrift. Does not construct a PyIceberg
catalog or load ``metadata.json``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from hive_metastore import ThriftHiveMetastore
from thrift.protocol import TBinaryProtocol
from thrift.transport import TSocket, TTransport

from cryo.sources.locations import (
    CatalogTableLocation,
    call_hms,
    list_hive_table_locations,
)

DEFAULT_HMS_PORT = 9083
_HMS_URI_SCHEMES = {"thrift", "hive", "tcp"}
# Connect must fail fast when HMS is on a private IP we cannot reach (VPN / leftover
# prod source in a local DB). After the socket is open, listing can take longer.
HMS_CONNECT_TIMEOUT_MS = 5_000
HMS_RPC_TIMEOUT_MS = 120_000


def hms_uri_from_config(config: Mapping[str, Any]) -> str:
    for key in ("uri", "catalog_uri", "hive.metastore.uris"):
        value = config.get(key)
        if value:
            return str(value).split(",")[0].strip()
    raise ValueError("Hive source config has no thrift uri")


def parse_hms_thrift_uri(uri: str) -> tuple[str, int]:
    """Return ``(host, port)`` from ``thrift://host:9083`` or ``host:port``."""
    raw = uri.strip()
    if not raw:
        raise ValueError("Hive Metastore uri is empty")
    parsed = urlparse(raw)
    if parsed.scheme.lower() in _HMS_URI_SCHEMES:
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid Hive Metastore uri: {uri!r}")
        return host, parsed.port or DEFAULT_HMS_PORT
    host, separator, port_text = raw.partition(":")
    if host and separator and port_text.isdigit():
        return host, int(port_text)
    if host and not separator:
        return host, DEFAULT_HMS_PORT
    raise ValueError(f"Invalid Hive Metastore uri: {uri!r}")


@contextmanager
def open_hms_thrift_client(uri: str) -> Iterator[Any]:
    """Open a ``ThriftHiveMetastore.Client`` against ``uri`` and close the transport."""
    host, port = parse_hms_thrift_uri(uri)
    socket = TSocket.TSocket(host, port)
    socket.setTimeout(HMS_CONNECT_TIMEOUT_MS)
    transport = TTransport.TBufferedTransport(socket)
    protocol = TBinaryProtocol.TBinaryProtocol(transport)
    client = ThriftHiveMetastore.Client(protocol)
    transport.open()
    socket.setTimeout(HMS_RPC_TIMEOUT_MS)
    try:
        yield client
    finally:
        try:
            transport.close()
        except Exception:
            pass


def list_hive_table_locations_from_uri(uri: str) -> list[CatalogTableLocation]:
    """List HMS ``sd.location`` for every table via a direct Thrift session."""
    with open_hms_thrift_client(uri) as client:
        return list_hive_table_locations(client)


def list_hive_databases_from_uri(uri: str) -> list[str]:
    """Every database name in the metastore -- one cheap call, no table enumeration."""
    with open_hms_thrift_client(uri) as client:
        return [str(name) for name in call_hms(client, "get_all_databases")]


def list_hive_table_locations_for_databases(
    uri: str, databases: list[str]
) -> list[CatalogTableLocation]:
    """``sd.location`` for the tables in just these databases.

    Lets a caller walk a large metastore in slices so partial results can be shown while
    the rest is still going, rather than holding everything back until the full 14k-table
    sweep completes.
    """
    with open_hms_thrift_client(uri) as client:
        return list_hive_table_locations(client, databases=databases)
