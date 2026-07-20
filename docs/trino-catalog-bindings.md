"""Notes for Trino catalog_bindings (destination ↔ Cryo lakehouse linking).

Trino destinations store an optional ``config.catalog_bindings`` list:

```json
[
  {"source_id": "<uuid>", "trino_catalog": "iceberg"}
]
```

- ``source_id`` — Cryo lakehouse (source) whose tables may use presets targeting this engine.
- ``trino_catalog`` — optional SQL catalog name when it differs from the source's
  ``catalog_name`` / name (e.g. local REST source ``rest`` registered in Trino as ``iceberg``).
- ``config.catalog`` remains the Trino session default for connection probes.

Preset assignment (API + UI) hides/rejects Trino presets when the table's source is not bound.
Non-Trino engines are unaffected. See ``cryo/engines/trino/catalog_bindings.py``, Connections
engine drawer, and ``GET /api/v1/tables/{id}/compatible-presets``.
"""
