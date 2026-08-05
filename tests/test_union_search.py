from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from cryo.db.models import Source, Table
from tests.auth_helpers import OPERATOR_HEADERS as AUTH_HEADERS
from tests.test_presets_api_db import ApiDb


def test_unionable_endpoint(api_db: ApiDb) -> None:
    client = api_db.client
    with api_db.session_factory() as session:
        source = Source(
            id=uuid.uuid4(),
            name="minio-local",
            type="iceberg",
            config={
                "type": "rest",
                "uri": "http://localhost:8181",
                "warehouse": "s3a://lake-bucket",
            },
            enabled=True,
        )
        session.add(source)

        target_table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="silver_data",
            table_name="target_table",
            is_active=True,
        )
        cand_table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="silver_data",
            table_name="cand_table",
            is_active=True,
        )
        session.add(target_table)
        session.add(cand_table)
        session.commit()

        target_id = target_table.id

    with patch("cryo.ai.union_search._fetch_table_columns") as mock_fetch:
        mock_fetch.side_effect = lambda session, table, cache: (
            [
                ("id", "integer"),
                ("name", "string"),
            ]
            if table.table_name == "target_table"
            else [
                ("id", "integer"),
                ("name", "string"),
                ("extra", "string"),
            ]
        )

        response = client.get(
            f"/api/v1/advanced/unionable/{target_id}",
            headers=AUTH_HEADERS,
        )

        print("RESPONSE STATUS:", response.status_code)
        print("RESPONSE BODY:", response.text)
        assert response.status_code == 200


def test_unionable_endpoint_handles_parser_error(api_db: ApiDb) -> None:
    client = api_db.client
    with api_db.session_factory() as session:
        source = Source(
            id=uuid.uuid4(),
            name="minio-local-2",
            type="iceberg",
            config={
                "type": "rest",
                "uri": "http://localhost:8181",
                "warehouse": "s3a://lake-bucket",
            },
            enabled=True,
        )
        session.add(source)

        target_table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="silver_data",
            table_name="target_table_2",
            is_active=True,
        )
        cand_table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="silver_data",
            table_name="cand_table_2",
            is_active=True,
        )
        session.add(target_table)
        session.add(cand_table)
        session.commit()

        target_id = target_table.id

    with patch("cryo.ai.union_search._extract_columns_from_table_obj") as mock_extract:
        # Mock load_table to return a fake table object while schema extraction fails.
        mock_extract.side_effect = TypeError("Mock uniterable schema fields error")

        with patch("cryo.sources.factory.source_catalog_for") as mock_catalog_factory:
            mock_catalog = MagicMock()
            mock_catalog.load_table.return_return = MagicMock()
            mock_catalog_factory.return_value = mock_catalog

            response = client.get(
                f"/api/v1/advanced/unionable/{target_id}",
                headers=AUTH_HEADERS,
            )

            # A target-schema parse failure returns no candidates instead of a 500.
            assert response.status_code == 200
            assert response.json()["results"] == []
