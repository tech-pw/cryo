"""Models that rely on a database-generated id must get that default from the migrations.

Tests build the schema with Base.metadata.create_all(), which applies the model's
server_default; production is built by the Alembic chain, which only has what a migration
wrote. 20260928_1810 created the assistant tables without it and every "Ask Cryo" message
failed in production with a NotNullViolation while the suite stayed green.
"""

from __future__ import annotations

import re
from pathlib import Path

from cryo.db.models import Base

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _needs_db_uuid(table) -> bool:  # type: ignore[no-untyped-def]
    column = table.columns.get("id")
    if column is None or not column.primary_key or column.server_default is None:
        return False
    return "gen_random_uuid" in str(getattr(column.server_default, "arg", ""))


def _create_block(source: str, table: str) -> str | None:
    match = re.search(rf'create_table\(\s*"{table}"', source)
    if not match:
        return None
    return source[match.start() : match.start() + 1500]


def test_every_db_generated_uuid_id_has_a_migrated_default() -> None:
    sources = {path.name: path.read_text() for path in sorted(VERSIONS.glob("*.py"))}
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not _needs_db_uuid(table):
            continue
        blocks = (_create_block(text, table.name) for text in sources.values())
        created = [block for block in blocks if block]
        if not created:
            continue  # created outside the chain (e.g. a raw SQL migration); not checked here
        block = created[0]
        if "uuid_pk()" in block:
            continue  # the initial schema's helper carries server_default=gen_random_uuid()
        start = re.search(r'Column\(\s*"id"', block)
        window = block[start.start() : start.start() + 250] if start else ""
        id_column = window.split("sa.Column(")[0]
        if "gen_random_uuid" in id_column or "server_default" in id_column:
            continue
        altered = any(
            "SET DEFAULT gen_random_uuid()" in text and f'"{table.name}"' in text
            for text in sources.values()
        )
        if not altered:
            missing.append(table.name)
    assert missing == [], f"tables whose migrated id has no gen_random_uuid() default: {missing}"
