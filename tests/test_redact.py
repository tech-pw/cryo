from cryo.api.redact import REDACTED_PLACEHOLDER, unredact_merge


def test_unredact_merge_substitutes_placeholder():
    stored = {"uri": "thrift://host:9083", "s3.secret-access-key": "real-secret"}
    new = {"uri": "thrift://host:9083", "s3.secret-access-key": REDACTED_PLACEHOLDER}

    merged = unredact_merge(new, stored)

    assert merged["s3.secret-access-key"] == "real-secret"


def test_unredact_merge_preserves_keys_absent_from_partial_payload():
    """A caller submitting only a subset of config keys (e.g. adding one new field) must not
    wipe every other stored key -- this is the exact bug that broke a live production source."""
    stored = {
        "uri": "thrift://host:9083",
        "s3.region": "ap-south-1",
        "catalog_name": "cdp",
        "s3.access-key-id": "AKIA...",
        "s3.secret-access-key": "real-secret",
    }
    new = {"warehouse": "s3://bucket/"}

    merged = unredact_merge(new, stored)

    assert merged == {
        "uri": "thrift://host:9083",
        "s3.region": "ap-south-1",
        "catalog_name": "cdp",
        "s3.access-key-id": "AKIA...",
        "s3.secret-access-key": "real-secret",
        "warehouse": "s3://bucket/",
    }


def test_unredact_merge_overwrites_existing_key_with_new_value():
    stored = {"uri": "thrift://old:9083", "warehouse": "s3://old/"}
    new = {"warehouse": "s3://new/"}

    merged = unredact_merge(new, stored)

    assert merged == {"uri": "thrift://old:9083", "warehouse": "s3://new/"}


def test_unredact_merge_nested_dict_preserves_siblings():
    stored = {"nested": {"a": 1, "b": "secret-b"}}
    new = {"nested": {"a": 2}}

    merged = unredact_merge(new, stored)

    assert merged == {"nested": {"a": 2, "b": "secret-b"}}
