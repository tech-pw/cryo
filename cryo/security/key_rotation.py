"""Re-encrypt every stored secret with the current CRYO_SECRET_KEY.

Rollover: deploy with the new CRYO_SECRET_KEY and the old one as CRYO_SECRET_KEY_PREVIOUS (every
process can then read both), run `cryo rotate-secret-key --apply`, then drop the previous key.
Encrypted values are strings prefixed ``enc:``, in text columns or nested anywhere inside JSON
columns, so this walks the ORM metadata rather than a hand-kept list of fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.security.crypto import _PREFIX, _fernet, fernet_for

_TEXTUAL = (sa.String, sa.Text, sa.JSON)


@dataclass
class RotationReport:
    rotated: dict[str, int] = field(default_factory=dict)
    undecryptable: dict[str, int] = field(default_factory=dict)

    @property
    def total_rotated(self) -> int:
        return sum(self.rotated.values())

    @property
    def total_undecryptable(self) -> int:
        return sum(self.undecryptable.values())


class _Unreadable(Exception):
    pass


def _rotate_string(value: str) -> str | None:
    """None when the value is already readable with the current key."""
    raw = value.removeprefix(_PREFIX).encode("ascii")
    try:
        fernet_for(get_settings().secret_key).decrypt(raw)
        return None
    except InvalidToken:
        pass
    try:
        token = _fernet().rotate(raw)
    except InvalidToken as exc:
        raise _Unreadable from exc
    return f"{_PREFIX}{token.decode('ascii')}"


def _rotate_value(value: Any, counts: list[int]) -> Any:
    """Return value with every enc: string re-encrypted; counts = [rotated, unreadable]."""
    if isinstance(value, str):
        if not value.startswith(_PREFIX):
            return value
        try:
            rotated = _rotate_string(value)
        except _Unreadable:
            counts[1] += 1
            return value
        if rotated is None:
            return value
        counts[0] += 1
        return rotated
    if isinstance(value, dict):
        return {key: _rotate_value(item, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [_rotate_value(item, counts) for item in value]
    return value


def _candidate_columns(metadata: sa.MetaData) -> Iterator[tuple[sa.Table, sa.Column[Any]]]:
    for table in metadata.sorted_tables:
        if not table.primary_key.columns:
            continue
        for column in table.columns:
            if isinstance(column.type, _TEXTUAL) and not column.primary_key:
                yield table, column


def rotate_secrets(session: Session, metadata: sa.MetaData, *, apply: bool) -> RotationReport:
    """Re-encrypt enc: values in place. Writes only when apply is True and nothing is unreadable."""
    report = RotationReport()
    updates: list[tuple[sa.Table, dict[str, Any], str, Any]] = []
    for table, column in _candidate_columns(metadata):
        pk = list(table.primary_key.columns)
        rows = session.execute(
            sa.select(*pk, column).where(sa.cast(column, sa.Text).like(f"%{_PREFIX}%"))
        ).all()
        name = f"{table.name}.{column.name}"
        for row in rows:
            counts = [0, 0]
            new_value = _rotate_value(row[-1], counts)
            if counts[0]:
                report.rotated[name] = report.rotated.get(name, 0) + counts[0]
                key = {col.name: row[index] for index, col in enumerate(pk)}
                updates.append((table, key, column.name, new_value))
            if counts[1]:
                report.undecryptable[name] = report.undecryptable.get(name, 0) + counts[1]
    if apply and not report.undecryptable:
        for table, key, column_name, new_value in updates:
            condition = sa.and_(*(table.c[col] == val for col, val in key.items()))
            session.execute(sa.update(table).where(condition).values({column_name: new_value}))
        session.commit()
    else:
        session.rollback()
    return report
