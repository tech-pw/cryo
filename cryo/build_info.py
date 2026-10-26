"""Which Cryo build is running (#263).

The release images bake CRYO_VERSION, CRYO_GIT_SHA and CRYO_BUILD_DATE in as ENV (see the
Dockerfiles and .github/workflows/release-supply-chain.yml). A source checkout or a local
`docker build` without build args falls back to the package version with no SHA.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from cryo import __version__


@dataclass(frozen=True)
class BuildInfo:
    version: str
    git_sha: str | None
    built_at: str | None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def build_info() -> BuildInfo:
    return BuildInfo(
        version=_env("CRYO_VERSION") or __version__,
        git_sha=_env("CRYO_GIT_SHA"),
        built_at=_env("CRYO_BUILD_DATE"),
    )


def current_schema_revision() -> str | None:
    """The Alembic revision recorded in the database, or None if it can't be read."""
    from sqlalchemy import text

    from cryo.db.session import SessionLocal

    try:
        with SessionLocal() as session:
            row = session.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:
        return None
    return str(row[0]) if row else None
