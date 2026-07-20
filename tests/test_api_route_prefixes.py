"""Regression test for a double-prefix bug found while adding the AutoComp candidates endpoint:
cryo/api/extensions_routes/*.py each declared their own `prefix="/api/v1"` AND get included into
extensions.py's `router`, which already has that prefix -- doubling every path in five files
(benefit, compliance, ai_features, qpp, triggers) to /api/v1/api/v1/..., unreachable at the path
the OpenAPI schema (and the frontend api client) actually expects.
"""

from __future__ import annotations


def test_extensions_sub_routers_are_not_double_prefixed(api_db) -> None:
    openapi = api_db.client.get("/openapi.json").json()
    paths = set(openapi.get("paths", {}).keys())

    assert not any(p.startswith("/api/v1/api/v1") for p in paths), (
        "found a double-prefixed path -- an extensions_routes sub-router regained its own "
        "prefix='/api/v1' after being included into extensions.py's already-prefixed router"
    )

    # One representative, previously-broken path per fixed file.
    for expected in (
        "/api/v1/tables/{table_id}/benefit-scores",
        "/api/v1/tables/{table_id}/effectiveness",
        "/api/v1/tables/{table_id}/autocomp/candidates",
        "/api/v1/tables/{table_id}/compliance",
        "/api/v1/tables/{table_id}/protected-snapshots",
    ):
        assert expected in paths, f"expected route not registered: {expected}"
