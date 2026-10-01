from __future__ import annotations

from datetime import date

from cryo.scanner.s3_request_cost_ingest import _line_bucket, locate_date_partition


class _FlatClient:
    """Only flat SimplePrefix keys exist; no CommonPrefixes."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def list_objects_v2(self, **kw):
        prefix = kw["Prefix"]
        if kw.get("Delimiter"):
            return {"CommonPrefixes": []}
        hits = [{"Key": k} for k in self.keys if k.startswith(prefix)]
        return {"Contents": hits[: kw.get("MaxKeys", 1000)]}


def test_flat_delivery_is_located_by_day_prefix() -> None:
    client = _FlatClient(["logs/2026-09-30-10-00-00-ABCD"])
    found = locate_date_partition(
        client, bucket="b", prefix="logs/", target_date=date(2026, 9, 30), source_bucket="src"
    )
    assert found == "logs/2026-09-30-"
    assert not found.endswith("/")


def test_flat_delivery_missing_day_returns_none() -> None:
    client = _FlatClient(["logs/2026-09-29-10-00-00-ABCD"])
    assert (
        locate_date_partition(client, bucket="b", prefix="logs/", target_date=date(2026, 9, 30))
        is None
    )


def test_line_bucket_reads_second_field() -> None:
    line = "owner my-bucket [01/Oct/2026:00:00:00 +0000] 1.2.3.4 arn:x REQ REST.GET.OBJECT k x"

    assert _line_bucket(line) == "my-bucket"
    assert _line_bucket("garbage") is None
