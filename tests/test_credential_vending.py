"""Tests for REST catalog credential vending refresh."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from cryo.catalogs.credential_vending import RestCatalogCredentialVendor, VendedCredentials


def test_refresh_if_needed_uses_cache() -> None:
    async def _run() -> None:
        vendor = RestCatalogCredentialVendor(
            catalog_url="http://catalog",
            oauth_client_id="id",
            oauth_client_secret="secret",
        )
        cached = VendedCredentials(
            access_key_id="ak",
            secret_access_key="sk",
            session_token="tok",
            expiration=datetime.now(UTC) + timedelta(hours=1),
            scope="ns.t",
        )
        vendor._vended_cache["ns.t"] = cached
        result = await vendor.refresh_if_needed("ns", "t")
        assert result is cached

    asyncio.run(_run())


def test_refresh_if_needed_vends_when_expired() -> None:
    async def _run() -> None:
        vendor = RestCatalogCredentialVendor(
            catalog_url="http://catalog",
            oauth_client_id="id",
            oauth_client_secret="secret",
        )
        expired = VendedCredentials(
            access_key_id="ak",
            secret_access_key="sk",
            session_token="tok",
            expiration=datetime.now(UTC) - timedelta(minutes=1),
            scope="ns.t",
        )
        vendor._vended_cache["ns.t"] = expired
        fresh = VendedCredentials(
            access_key_id="ak2",
            secret_access_key="sk2",
            session_token="tok2",
            expiration=datetime.now(UTC) + timedelta(hours=1),
            scope="ns.t",
        )
        with patch.object(vendor, "vend_credentials", AsyncMock(return_value=fresh)):
            result = await vendor.refresh_if_needed("ns", "t")
        assert result.access_key_id == "ak2"

    asyncio.run(_run())
