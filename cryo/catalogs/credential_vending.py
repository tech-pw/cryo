"""REST Catalog credential vending for Polaris/Unity Catalog.

Enables short-lived, table-scoped STS tokens instead of long-lived AWS keys.
Cryo authenticates to catalog with OAuth2, receives vended credentials for S3 access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class VendedCredentials:
    """Short-lived credentials vended by REST catalog."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None
    expiration: datetime
    scope: str  # Table/namespace scope


class RestCatalogCredentialVendor:
    """Vends short-lived credentials from REST catalog (Polaris, Unity Catalog)."""

    def __init__(
        self,
        catalog_url: str,
        oauth_client_id: str,
        oauth_client_secret: str,
        oauth_token_url: str | None = None,
    ):
        self.catalog_url = catalog_url.rstrip("/")
        self.oauth_client_id = oauth_client_id
        self.oauth_client_secret = oauth_client_secret
        self.oauth_token_url = oauth_token_url or f"{self.catalog_url}/v1/oauth/tokens"
        
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def get_access_token(self) -> str:
        """
        Get OAuth2 access token for REST catalog authentication.
        
        Returns:
            Bearer token for catalog API
        """
        # Check if cached token is still valid
        if (
            self._access_token
            and self._token_expires_at
            and self._token_expires_at > datetime.now(UTC) + timedelta(minutes=5)
        ):
            return self._access_token
        
        # Request new token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.oauth_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.oauth_client_id,
                    "client_secret": self.oauth_client_secret,
                    "scope": "PRINCIPAL_ROLE:ALL",
                },
            )
            response.raise_for_status()
            
            token_data = response.json()
            self._access_token = token_data["access_token"]
            
            # Calculate expiration (typically 3600 seconds)
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
            
            return self._access_token

    async def vend_credentials(
        self,
        namespace: str,
        table_name: str,
    ) -> VendedCredentials:
        """
        Vend short-lived, table-scoped S3 credentials from REST catalog.
        
        Args:
            namespace: Catalog namespace (e.g., "warehouse.schema")
            table_name: Table name
        
        Returns:
            Vended credentials with STS token
        """
        token = await self.get_access_token()
        
        # Call catalog's load table API which includes credential vending
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.catalog_url}/v1/{namespace}/tables/{table_name}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            
            table_metadata = response.json()
            
            # Extract vended credentials from table metadata
            # Polaris returns credentials in "config" section
            config = table_metadata.get("config", {})
            
            access_key = config.get("s3.access-key-id")
            secret_key = config.get("s3.secret-access-key")
            session_token = config.get("s3.session-token")
            
            if not access_key or not secret_key:
                raise ValueError("Catalog did not vend credentials")
            
            # Expiration is typically 1 hour for STS tokens
            expiration = datetime.now(UTC) + timedelta(hours=1)
            
            return VendedCredentials(
                access_key_id=access_key,
                secret_access_key=secret_key,
                session_token=session_token,
                expiration=expiration,
                scope=f"{namespace}.{table_name}",
            )

    async def get_s3_client_for_table(
        self,
        namespace: str,
        table_name: str,
    ) -> Any:
        """
        Get boto3 S3 client with vended credentials for a specific table.
        
        Args:
            namespace: Catalog namespace
            table_name: Table name
        
        Returns:
            boto3 S3 client configured with vended credentials
        """
        import boto3
        
        creds = await self.vend_credentials(namespace, table_name)
        
        # Create S3 client with short-lived credentials
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
        )
        
        logger.info(f"Vended credentials for {namespace}.{table_name}, expires at {creds.expiration}")
        
        return s3_client


# Integration with IcebergSource:
#
# In cryo/sources/iceberg.py, add credential vending support:
#
# class IcebergSource:
#     def __init__(
#         self,
#         catalog_url: str,
#         credential_vending_enabled: bool = False,
#         oauth_client_id: str | None = None,
#         oauth_client_secret: str | None = None,
#     ):
#         self.credential_vending_enabled = credential_vending_enabled
#         
#         if credential_vending_enabled:
#             self.credential_vendor = RestCatalogCredentialVendor(
#                 catalog_url=catalog_url,
#                 oauth_client_id=oauth_client_id,
#                 oauth_client_secret=oauth_client_secret,
#             )
#     
#     async def load_table(self, namespace: str, table_name: str):
#         if self.credential_vending_enabled:
#             # Use vended credentials
#             s3_client = await self.credential_vendor.get_s3_client_for_table(
#                 namespace, table_name
#             )
#             # Configure PyIceberg to use this S3 client
#         else:
#             # Use static AWS credentials from environment/config
#             pass
