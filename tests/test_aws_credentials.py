"""Tests for cryo.integrations.aws_credentials -- credential resolution helpers.

Same in-memory session pattern as test_aws_integration_api.py: plain function calls against the
session fixture, no HTTP layer.
"""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import AWSIntegration, Source
from cryo.integrations.aws_credentials import credentials_for_source
from cryo.security.crypto import encrypt_secret


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    session.flush()
    return source


def test_credentials_for_source_returns_credentials_for_enabled_integration(session) -> None:
    source = _seed_source(session)
    session.add(
        AWSIntegration(
            source_id=source.id,
            bucket="my-bucket",
            region="ap-south-1",
            access_key_id="AKIA123",
            secret_ref=encrypt_secret("shh"),
            enabled=True,
        )
    )
    session.flush()

    credentials = credentials_for_source(session, source.id)

    assert credentials is not None
    assert credentials.region == "ap-south-1"
    assert credentials.access_key_id == "AKIA123"
    assert credentials.secret_access_key == "shh"


def test_credentials_for_source_returns_none_when_no_integration(session) -> None:
    source = _seed_source(session)

    assert credentials_for_source(session, source.id) is None


def test_credentials_for_source_returns_none_when_disabled(session) -> None:
    source = _seed_source(session)
    session.add(
        AWSIntegration(
            source_id=source.id,
            bucket="my-bucket",
            region="us-east-1",
            access_key_id="AKIA123",
            secret_ref=encrypt_secret("shh"),
            enabled=False,
        )
    )
    session.flush()

    assert credentials_for_source(session, source.id) is None


def test_credentials_for_source_returns_none_when_secret_missing(session) -> None:
    source = _seed_source(session)
    session.add(
        AWSIntegration(
            source_id=source.id,
            bucket="my-bucket",
            region="us-east-1",
            access_key_id="AKIA123",
            secret_ref=None,
            enabled=True,
        )
    )
    session.flush()

    assert credentials_for_source(session, source.id) is None
