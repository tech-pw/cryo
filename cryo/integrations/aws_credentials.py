"""Resolve AWS integration credentials with at-rest decryption."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def credentials_for_source(session: Session, source_id: uuid.UUID) -> AWSCredentials | None:
    """Resolve a source's ``AWSIntegration`` credentials, if one exists, is enabled, and has
    a usable secret.

    Returns ``None`` (never raises) on any of: no ``AWSIntegration`` row for this source,
    the row exists but is disabled, or the stored secret fails to decrypt -- all of these are
    "this source doesn't have live AWS pricing available" from the caller's point of view, not
    exceptional. This is the single shared lookup-and-construct helper other call sites (e.g.
    ``cryo/api/routes.py``'s ``get_aws_usage``) can migrate onto instead of each re-implementing
    the same ``select(AWSIntegration).where(...)`` + enabled-check pattern independently.
    """
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None or not integration.enabled:
        return None
    try:
        return credentials_from_integration(integration)
    except ValueError:
        return None
