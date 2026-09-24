"""Classifies a table's ``last_scan_error`` text into a small, actionable category.

Display-only: this never changes scan behavior, only how a failure is labeled for an operator
looking at the Broken Tables panel. Deliberately conservative -- only classifies patterns
actually confirmed against real production error text (see the investigation that produced
this module: an initial hypothesis that "Path does not exist" errors were masked S3 Glacier
archival state turned out to be wrong once checked against the real error text -- zero of
thousands of broken tables actually contained AWS's ``INVALID_OBJECT_STATE`` error code, the
signature Glacier/Deep-Archive failures actually produce (HeadObject succeeds on an archived
object; only GetObject fails, with that specific code). Add a category here only once you've
confirmed the AWS error code it's keyed on actually appears in real ``last_scan_error`` text --
a wrong-but-confident category is worse than an honest "Other".
"""

from __future__ import annotations

from typing import Literal

FailureCategory = Literal["permission_denied", "metadata_not_found", "manifest_missing", "other"]

_CATEGORY_LABELS: dict[FailureCategory, str] = {
    "permission_denied": "Permission denied",
    "metadata_not_found": "Metadata not found",
    "manifest_missing": "Manifest missing",
    "other": "Other",
}

# Whether this category is something an operator can act on from within Cryo, or strictly
# requires an out-of-band fix (an AWS IAM policy change, an investigation into why a catalog
# pointer is stale, etc.). Surfaced so the UI can set expectations honestly rather than imply
# every broken table has a one-click fix.
_ACTIONABLE_FROM_CRYO: dict[FailureCategory, bool] = {
    "permission_denied": False,
    "metadata_not_found": False,
    "manifest_missing": False,
    "other": False,
}

# Categories where the files the table's own metadata names are gone from object storage, so
# re-running the scan produces the identical error forever. Counted apart from scan failures
# (see ScanSummary.unreadable) so the failure rate stays a signal about things that can be
# fixed. Permission denied is deliberately absent: an IAM policy change does fix those, and
# burying thousands of them in "unreadable" would hide a real, correctable misconfiguration.
_PERMANENTLY_UNREADABLE: frozenset[str] = frozenset({"metadata_not_found", "manifest_missing"})

_CATEGORY_GUIDANCE: dict[FailureCategory, str] = {
    "permission_denied": (
        "Cryo's AWS credentials were denied reading this table's current metadata file. "
        "Check the IAM policy attached to the source's credentials covers this bucket/prefix."
    ),
    "metadata_not_found": (
        "The table's catalog entry points at a metadata file that doesn't exist in S3. This "
        "usually means the underlying data was deleted or moved outside Cryo, or the catalog's "
        "pointer is stale -- investigate before assuming this table is safe to drop."
    ),
    "manifest_missing": (
        "The table's metadata file was read, but the manifest list it points at is gone from "
        "S3. Trino fails on these tables too, so this is a broken table in the lakehouse, not "
        "a Cryo problem -- the owning team has to repoint the catalog or restore the table."
    ),
    "other": "See the raw error below for detail.",
}


def classify_scan_failure(error: str | None) -> FailureCategory | None:
    """Return a category for a scan-failure error string, or ``None`` if there's no error."""
    if not error:
        return None
    # The Go engine reports a denied read as a bare HTTP 403 with code=Unknown rather than
    # AWS's ACCESS_DENIED string, so keying only on the latter labelled real permission
    # problems "Other" (one such case in the 2026-09-23 fleet run). Checked before the
    # missing-object markers below, which key on 404 and cannot collide.
    if "ACCESS_DENIED" in error or _has_status(error, 403):
        return "permission_denied"
    if _is_manifest_missing(error):
        return "manifest_missing"
    if _is_metadata_file_missing(error) or _is_schemeless_metadata_pointer(error):
        return "metadata_not_found"
    if "Path does not exist" in error or "No such file or directory" in error:
        return "metadata_not_found"
    return "other"


# Real text from a cdp.central scan on 2026-09-23, which returned this for 201 of 585 tables:
#   RuntimeError: load manifests: could not open manifest file: blob (key
#   "silver/central/<table>/metadata/snap-<id>-1-<uuid>.avro") (code=NotFound): ... NoSuchKey
# Checked against Trino on the same metastore: it names the same missing file and fails too.
_MISSING_OBJECT_MARKERS = ("nosuchkey", "code=notfound", "status code: 404", "statuscode: 404")


def _has_status(error: str, code: int) -> bool:
    lowered = error.lower()
    return f"statuscode: {code}" in lowered or f"status code: {code}" in lowered


def _is_manifest_missing(error: str) -> bool:
    """A missing-object error *about a manifest*, not about arbitrary table data.

    Both halves are required. "NoSuchKey" alone also covers a missing data file mid-scan,
    which is a different (and often transient) problem, and naming a manifest alone covers
    ordinary read errors that a retry can clear.
    """
    lowered = error.lower()
    return "manifest" in lowered and any(marker in lowered for marker in _MISSING_OBJECT_MARKERS)


# The metadata.json case is the *larger* half and was missed when this module first shipped,
# because it matches none of the patterns above -- it names no manifest, and the Go engine
# words it as a 404 rather than "Path does not exist". Real text from the 2026-09-23 00:00 UTC
# fleet run, where it was 267 of one chunk's 339 missing-object failures (the manifest case
# above was the other 74):
#   RuntimeError: failed to load table: blob (key
#   "bronze/ai_ncert/chapters_evolving/metadata/00000-248835ae-....metadata.json")
#   (code=NotFound): operation error S3: GetObject, https response error StatusCode: 404
def _is_metadata_file_missing(error: str) -> bool:
    """A missing-object error naming the table's own ``.metadata.json``.

    Same two-part rule as _is_manifest_missing, for the same reason: a bare 404 can be a
    transient read of some other object, and merely naming a metadata file covers errors a
    retry can clear. Only the combination means the catalog pointer targets an object that
    is not there.
    """
    lowered = error.lower()
    return ".metadata.json" in lowered and any(
        marker in lowered for marker in _MISSING_OBJECT_MARKERS
    )


# The catalog's metadata_location has no bucket or scheme, so the Go engine opens it as a local
# path. Real text, 2026-09-24, cdp youtube.silver_videos (location NULL; the owning team rebuilt
# it as silver_videos_fixed at the real S3 path):
#   failed to load table: open /youtube/videos/metadata/00009-7022....metadata.json:
#   invalid argument
def _is_schemeless_metadata_pointer(error: str) -> bool:
    lowered = error.lower()
    return "failed to load table: open /" in lowered and ".metadata.json" in lowered


def scan_failure_is_permanent(error: str | None) -> bool:
    """True when re-running the scan cannot possibly produce a different result."""
    category = classify_scan_failure(error)
    return category in _PERMANENTLY_UNREADABLE


def failure_category_label(category: FailureCategory | None) -> str | None:
    return _CATEGORY_LABELS.get(category) if category else None


def failure_category_actionable_from_cryo(category: FailureCategory | None) -> bool:
    return _ACTIONABLE_FROM_CRYO.get(category, False) if category else False


def failure_category_guidance(category: FailureCategory | None) -> str | None:
    return _CATEGORY_GUIDANCE.get(category) if category else None
