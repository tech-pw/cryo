from cryo.ai.tools.read_docs import _excerpt, _iceberg_url


def test_iceberg_url_picks_the_maintenance_page() -> None:
    assert _iceberg_url("expire snapshots") == "https://iceberg.apache.org/docs/latest/maintenance/"
    assert _iceberg_url("spark procedure") == (
        "https://iceberg.apache.org/docs/latest/spark-procedures/"
    )


def test_excerpt_keeps_the_matching_sentence() -> None:
    text = "intro " * 40 + "Expire snapshots removes old snapshots."
    excerpt = _excerpt(text, "expire snapshots")
    assert "Expire snapshots" in excerpt
