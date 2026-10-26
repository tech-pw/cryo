"""Split a dotted table reference into its name parts. Dialects quote the parts themselves."""

from __future__ import annotations

import re

# A part is either a double-quoted identifier ("" escapes a quote) or a bare run without
# dots or quotes. Table names reach the renderers straight from catalog discovery, so they
# are untrusted input to the SQL we emit.
_PART = re.compile(r'"((?:[^"]|"")*)"|([^."]+)')


def split_table_ref(table_ref: str) -> list[str]:
    """Return the unquoted parts of ``[catalog.]namespace....table``, or raise ValueError."""
    parts: list[str] = []
    pos = 0
    while True:
        match = _PART.match(table_ref, pos)
        if match is None:
            raise ValueError(f"malformed table reference: {table_ref!r}")
        quoted, bare = match.groups()
        part = quoted.replace('""', '"') if quoted is not None else bare
        if not part or "\x00" in part:
            raise ValueError(f"malformed table reference: {table_ref!r}")
        parts.append(part)
        pos = match.end()
        if pos == len(table_ref):
            break
        if table_ref[pos] != ".":
            raise ValueError(f"malformed table reference: {table_ref!r}")
        pos += 1
    if len(parts) < 2:
        raise ValueError(f"table reference needs at least schema.table: {table_ref!r}")
    return parts
