"""Resolve named tables for a turn. Ambiguous names are not guessed."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from cryo.ai.tools.read_identity import candidate, find_tables
from cryo.api.security import Principal
from cryo.db.models import Table

_QUALIFIED = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")
_TABLE_PHRASE = re.compile(r"\btable\s+([A-Za-z_][\w]*)\b", re.IGNORECASE)


def _heading_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not re.fullmatch(r"[A-Za-z_][\w]*", name):
        return None
    if "_" not in name and not any(char.isdigit() for char in name):
        return None
    return name


def screen_block(resolved: dict[str, Any], page: dict[str, Any] | None = None) -> str:
    """Tell the model which table or run is on screen. Do not ask the operator again."""
    page = page or {}
    lines = [
        "The operator is looking at this screen. Use it. Do not ask which table if one is named."
    ]
    entities = resolved.get("entities") or []
    if entities:
        entity = entities[0]
        lines.append(
            "Current table: "
            f"{entity.get('qualified_name')} (table_id {entity.get('table_id')}). "
            "When they say this table, that is the table."
        )
    for key, label in (("run_id", "Current run"), ("job_id", "Current job"), ("route", "Route")):
        value = page.get(key)
        if isinstance(value, str) and value:
            lines.append(f"{label}: {value}")
    heading = page.get("page_heading")
    if isinstance(heading, str) and heading:
        lines.append(f"Page heading: {heading}")
    return "\n".join(lines)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _lookup(
    session: Session,
    principal: Principal,
    schema_name: str | None,
    table_name: str,
) -> list[Table]:
    return find_tables(
        session,
        schema_name=schema_name,
        table_name=table_name,
        tenant_id=principal.tenant_id,
    )


def resolve(
    session: Session,
    principal: Principal,
    text: str,
    page_context: dict[str, Any] | None,
    mentions: list[Any] | None,
) -> dict[str, Any]:
    page = page_context or {}
    timezone = str(page.get("timezone") or "UTC")
    window = page.get("window")
    names: list[tuple[str | None, str, bool]] = []
    for match in _QUALIFIED.finditer(text or ""):
        names.append((match.group(1), match.group(2), False))
    for match in _TABLE_PHRASE.finditer(text or ""):
        token = match.group(1)
        if "." not in token:
            names.append((None, token, True))
    for mention in mentions or []:
        if isinstance(mention, str) and "." in mention:
            schema_name, table_name = mention.split(".", 1)
            names.append((schema_name, table_name, False))
        elif isinstance(mention, str) and mention:
            names.append((None, mention, True))
        elif isinstance(mention, dict):
            raw_name = mention.get("table_name") or mention.get("name")
            if isinstance(raw_name, str) and raw_name:
                raw_schema = mention.get("schema_name")
                names.append(
                    (
                        raw_schema if isinstance(raw_schema, str) else None,
                        raw_name,
                        not isinstance(raw_schema, str),
                    )
                )

    ambiguous = False
    candidates: list[dict[str, str]] = []
    entities: list[dict[str, str]] = []
    seen: set[str] = set()
    for schema_part, table_part, bare in names:
        matches = _lookup(session, principal, schema_part, table_part)
        if bare and schema_part is None and len(matches) > 1:
            ambiguous = True
            candidates.extend(candidate(table) for table in matches)
            continue
        if len(matches) > 1:
            ambiguous = True
            candidates.extend(candidate(table) for table in matches)
            continue
        if len(matches) == 1:
            entity = candidate(matches[0])
            if entity["table_id"] not in seen:
                seen.add(entity["table_id"])
                entities.append(entity)

    if ambiguous:
        entities = []
        candidates = _dedupe(candidates)
    elif not entities:
        page_id = _as_uuid(page.get("table_id"))
        if page_id is not None:
            table = session.get(Table, page_id)
            if table is not None and (
                principal.tenant_id is None or table.tenant_id == principal.tenant_id
            ):
                entities.append(candidate(table))
        if not entities:
            heading = _heading_name(page.get("page_heading"))
            if heading:
                matches = _lookup(session, principal, None, heading)
                if len(matches) == 1:
                    entities.append(candidate(matches[0]))
    return {
        "entities": entities,
        "window": window,
        "timezone": timezone,
        "ambiguous": ambiguous,
        "question": text,
        "candidates": candidates,
    }


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = row["table_id"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique
