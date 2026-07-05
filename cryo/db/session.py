from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from cryo.config import Settings, get_settings


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def get_session_maker(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the configured database."""
    bind = create_db_engine(settings)
    return sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)
