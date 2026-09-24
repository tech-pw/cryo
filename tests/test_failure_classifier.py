"""Tests for cryo/scanner/failure_classifier.py.

The classifier is deliberately narrow -- it only labels patterns confirmed against real
production error text. See the module docstring: an initial hypothesis that "Path does not
exist" errors were masked S3 Glacier archival state turned out to be wrong once checked
against real data (zero of thousands of broken tables contained AWS's INVALID_OBJECT_STATE
error code). These tests pin the classifier to what's actually verified, not what's plausible.
"""

from __future__ import annotations

from cryo.scanner.failure_classifier import (
    classify_scan_failure,
    failure_category_actionable_from_cryo,
    failure_category_guidance,
    failure_category_label,
    scan_failure_is_permanent,
)

# Verbatim from a cdp.central scan on 2026-09-23, which produced this for 201 of 585 tables.
MANIFEST_MISSING_ERROR = (
    "RuntimeError: load manifests: could not open manifest file: blob (key "
    '"silver/central/gold_polls_evolving/metadata/'
    'snap-2836956739000000000-1-d2373dc1-0000-0000-0000-000000000000.avro") '
    "(code=NotFound): operation error S3: GetObject, https response error StatusCode: 404, "
    "api error NoSuchKey: The specified key does not exist."
)


def test_classifies_access_denied_as_permission_denied() -> None:
    error = (
        "When reading information for key 'bronze/central/videos_evolving/metadata/"
        "00007-x.json' in bucket 'lake': AWS Error ACCESS_DENIED during HeadObject "
        "operation: No response body."
    )
    assert classify_scan_failure(error) == "permission_denied"


def test_classifies_path_not_found_as_metadata_not_found() -> None:
    error = (
        "[Errno 2] Path does not exist 'lake/bronze/ott/categories_evolving/metadata/"
        "00001-x.json'. Detail: [errno 2] No such file or directory"
    )
    assert classify_scan_failure(error) == "metadata_not_found"


def test_classifies_a_bare_http_403_as_permission_denied() -> None:
    """The Go engine words a denied read as a plain 403 with code=Unknown, with no
    ACCESS_DENIED string anywhere, so it used to be labelled "Other". Verbatim from the
    2026-09-23 fleet run. It must stay non-permanent -- an IAM fix resolves it."""
    error = (
        'RuntimeError: failed to load table: blob (key "bronze/comment_service/ratings_v1/'
        'metadata/00185-8ef46931-7274-4c41-aedf-46d18ceeee90.metadata.json") (code=Unknown): '
        "operation error S3: GetObject, https response error StatusCode: 403"
    )

    assert classify_scan_failure(error) == "permission_denied"
    assert scan_failure_is_permanent(error) is False


def test_classifies_unrecognized_error_as_other() -> None:
    assert classify_scan_failure("connection reset by peer") == "other"


def test_classifies_none_and_empty_as_none() -> None:
    assert classify_scan_failure(None) is None
    assert classify_scan_failure("") is None


def test_no_category_claims_actionable_from_cryo() -> None:
    """Every current category requires an out-of-band fix (IAM policy change, or manual
    investigation into a stale catalog pointer) -- none should claim a one-click Cryo fix."""
    for category in ("permission_denied", "metadata_not_found", "manifest_missing", "other"):
        assert failure_category_actionable_from_cryo(category) is False  # type: ignore[arg-type]


def test_every_category_has_a_label_and_guidance() -> None:
    for category in ("permission_denied", "metadata_not_found", "manifest_missing", "other"):
        assert failure_category_label(category) is not None  # type: ignore[arg-type]
        assert failure_category_guidance(category) is not None  # type: ignore[arg-type]


def test_classifies_a_missing_manifest_list_as_manifest_missing() -> None:
    assert classify_scan_failure(MANIFEST_MISSING_ERROR) == "manifest_missing"


def test_classifies_a_missing_metadata_json_as_metadata_not_found() -> None:
    """The larger half of the real failures, and the one this module missed when it first
    shipped: it names no manifest, and the Go engine words it as a 404 rather than "Path does
    not exist", so it fell through to "other" and kept counting as a retryable failure.
    Verbatim from the 2026-09-23 00:00 UTC fleet run (267 of one chunk's 339)."""
    error = (
        "RuntimeError: failed to load table: blob (key "
        '"bronze/ai_ncert/chapters_evolving/metadata/'
        '00000-248835ae-db78-4287-aae7-accae7860c8f.metadata.json") (code=NotFound): '
        "operation error S3: GetObject, https response error StatusCode: 404, "
        "RequestID: X1R9TFEZSSZ6SJ1T"
    )

    assert classify_scan_failure(error) == "metadata_not_found"
    assert scan_failure_is_permanent(error) is True


def test_a_metadata_pointer_with_no_bucket_is_metadata_not_found() -> None:
    """The catalog's metadata_location has no scheme, so the Go engine opens it as a local
    file. Verbatim from cdp youtube.silver_videos, 2026-09-24 (location NULL, superseded by
    silver_videos_fixed)."""
    error = (
        "failed to load table: open /youtube/videos/metadata/"
        "00009-7022095b-96a9-418b-8ff0-221c4999762c.metadata.json: invalid argument"
    )
    assert classify_scan_failure(error) == "metadata_not_found"
    assert scan_failure_is_permanent(error) is True


def test_a_metadata_json_read_error_that_is_not_a_missing_object_is_not_permanent() -> None:
    """Same two-part rule as the manifest case: naming a metadata file is not enough, or an
    ordinary read blip on a healthy table would be written off as permanently unreadable."""
    error = (
        'failed to load table: blob (key "bronze/x/y/metadata/00000-a.metadata.json"): '
        "connection reset by peer"
    )
    assert classify_scan_failure(error) == "other"
    assert scan_failure_is_permanent(error) is False


def test_a_missing_data_file_is_not_a_missing_manifest() -> None:
    """NoSuchKey on its own also covers a data file vanishing mid-scan, which a retry can
    clear. Only a missing *manifest* means the table's metadata itself is unreadable."""
    error = (
        'could not open data file: blob (key "silver/central/events/data/part-0.parquet") '
        "(code=NotFound): api error NoSuchKey: The specified key does not exist."
    )
    assert classify_scan_failure(error) == "other"


def test_a_manifest_read_error_that_is_not_a_missing_object_is_not_permanent() -> None:
    error = "load manifests: could not open manifest file: connection reset by peer"
    assert classify_scan_failure(error) == "other"
    assert scan_failure_is_permanent(error) is False


def test_missing_metadata_and_missing_manifests_are_permanent() -> None:
    assert scan_failure_is_permanent(MANIFEST_MISSING_ERROR) is True
    assert (
        scan_failure_is_permanent(
            "[Errno 2] Path does not exist 'lake/bronze/ott/x/metadata/00001-x.json'"
        )
        is True
    )


def test_permission_denied_is_not_permanent() -> None:
    """An IAM policy change fixes these, so they must keep counting as scan failures --
    burying thousands of them as "unreadable" would hide a correctable misconfiguration."""
    error = "AWS Error ACCESS_DENIED during HeadObject operation: No response body."
    assert scan_failure_is_permanent(error) is False


def test_a_healthy_scan_is_not_permanent_failure() -> None:
    assert scan_failure_is_permanent(None) is False
    assert scan_failure_is_permanent("") is False
