from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from cryo.config import DEFAULT_SECRET_KEY, Settings
from cryo.security import crypto
from cryo.security.key_rotation import rotate_secrets

OLD = DEFAULT_SECRET_KEY
NEW = "n" * 48


def _settings(secret_key: str, previous: str | None = None) -> Settings:
    return Settings(_env_file=None, secret_key=secret_key, secret_key_previous=previous)


@pytest.fixture
def use_keys():
    def _use(secret_key: str, previous: str | None = None):
        settings = _settings(secret_key, previous)
        return (
            patch("cryo.security.crypto.get_settings", return_value=settings),
            patch("cryo.security.key_rotation.get_settings", return_value=settings),
        )

    return _use


def _db() -> tuple[Session, sa.MetaData, sa.Table]:
    metadata = sa.MetaData()
    table = sa.Table(
        "sources",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_key", sa.Text),
        sa.Column("config", sa.JSON),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    return Session(engine), metadata, table


def test_previous_key_still_decrypts_after_the_key_changes(use_keys) -> None:
    a, b = use_keys(OLD)
    with a, b:
        stored = crypto.encrypt_secret("s3cr3t")
    a, b = use_keys(NEW)
    with a, b, pytest.raises(ValueError):
        crypto.decrypt_secret(stored)
    a, b = use_keys(NEW, previous=OLD)
    with a, b:
        assert crypto.decrypt_secret(stored) == "s3cr3t"


def test_rotation_reencrypts_text_and_nested_json_then_reports_zero(use_keys) -> None:
    session, metadata, table = _db()
    a, b = use_keys(OLD)
    with a, b:
        key = crypto.encrypt_secret("api-key")
        nested = crypto.encrypt_secret("s3-secret")
    session.execute(
        table.insert().values(
            id=1, api_key=key, config={"auth": {"secret": nested}, "uri": "http://x"}
        )
    )
    session.commit()

    a, b = use_keys(NEW, previous=OLD)
    with a, b:
        dry = rotate_secrets(session, metadata, apply=False)
        assert dry.rotated == {"sources.api_key": 1, "sources.config": 1}
        assert session.execute(sa.select(table.c.api_key)).scalar_one() == key

        applied = rotate_secrets(session, metadata, apply=True)
        assert applied.total_rotated == 2
        assert rotate_secrets(session, metadata, apply=False).total_rotated == 0

    row = session.execute(sa.select(table.c.api_key, table.c.config)).one()
    a, b = use_keys(NEW)
    with a, b:
        assert crypto.decrypt_secret(row.api_key) == "api-key"
        assert crypto.decrypt_secret(row.config["auth"]["secret"]) == "s3-secret"
        assert row.config["uri"] == "http://x"


def test_rotation_writes_nothing_when_a_value_is_unreadable(use_keys) -> None:
    session, metadata, table = _db()
    a, b = use_keys("x" * 40)
    with a, b:
        foreign = crypto.encrypt_secret("from-another-key")
    a, b = use_keys(OLD)
    with a, b:
        ours = crypto.encrypt_secret("ours")
    session.execute(table.insert().values(id=1, api_key=ours, config={"s": foreign}))
    session.commit()

    a, b = use_keys(NEW, previous=OLD)
    with a, b:
        report = rotate_secrets(session, metadata, apply=True)
    assert report.undecryptable == {"sources.config": 1}
    assert session.execute(sa.select(table.c.api_key)).scalar_one() == ours


def test_prod_accepts_the_default_as_the_previous_key() -> None:
    settings = Settings(
        _env_file=None,
        environment="prod",
        secret_key=NEW,
        jwt_secret=NEW,
        secret_key_previous=OLD,
    )
    assert settings.secret_key_previous == OLD
