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


def test_classifies_unrecognized_error_as_other() -> None:
    assert classify_scan_failure("connection reset by peer") == "other"


def test_classifies_none_and_empty_as_none() -> None:
    assert classify_scan_failure(None) is None
    assert classify_scan_failure("") is None


def test_no_category_claims_actionable_from_cryo() -> None:
    """Every current category requires an out-of-band fix (IAM policy change, or manual
    investigation into a stale catalog pointer) -- none should claim a one-click Cryo fix."""
    for category in ("permission_denied", "metadata_not_found", "other"):
        assert failure_category_actionable_from_cryo(category) is False  # type: ignore[arg-type]


def test_every_category_has_a_label_and_guidance() -> None:
    for category in ("permission_denied", "metadata_not_found", "other"):
        assert failure_category_label(category) is not None  # type: ignore[arg-type]
        assert failure_category_guidance(category) is not None  # type: ignore[arg-type]
