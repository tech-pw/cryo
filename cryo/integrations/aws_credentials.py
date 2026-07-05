"""Resolve AWS integration credentials with at-rest decryption."""

from __future__ import annotations

from cryo.db.models import AWSIntegration
from cryo.integrations.aws_s3_metrics import AWSCredentials
from cryo.security.crypto import decrypt_secret


def credentials_from_integration(integration: AWSIntegration) -> AWSCredentials:
    secret = decrypt_secret(integration.secret_ref)
    if secret is None:
        raise ValueError("AWS integration has no secret access key configured")
    return AWSCredentials(
        region=integration.region,
        access_key_id=integration.access_key_id,
        secret_access_key=secret,
    )
