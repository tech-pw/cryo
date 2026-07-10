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

FailureCategory = Literal["permission_denied", "metadata_not_found", "other"]

_CATEGORY_LABELS: dict[FailureCategory, str] = {
    "permission_denied": "Permission denied",
    "metadata_not_found": "Metadata not found",
    "other": "Other",
}

# Whether this category is something an operator can act on from within Cryo, or strictly
# requires an out-of-band fix (an AWS IAM policy change, an investigation into why a catalog
# pointer is stale, etc.). Surfaced so the UI can set expectations honestly rather than imply
# every broken table has a one-click fix.
_ACTIONABLE_FROM_CRYO: dict[FailureCategory, bool] = {
    "permission_denied": False,
    "metadata_not_found": False,
    "other": False,
}

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
    "other": "See the raw error below for detail.",
}


def classify_scan_failure(error: str | None) -> FailureCategory | None:
    """Return a category for a scan-failure error string, or ``None`` if there's no error."""
    if not error:
        return None
    if "ACCESS_DENIED" in error:
        return "permission_denied"
    if "Path does not exist" in error or "No such file or directory" in error:
        return "metadata_not_found"
    return "other"


def failure_category_label(category: FailureCategory | None) -> str | None:
    return _CATEGORY_LABELS.get(category) if category else None


def failure_category_actionable_from_cryo(category: FailureCategory | None) -> bool:
    return _ACTIONABLE_FROM_CRYO.get(category, False) if category else False


def failure_category_guidance(category: FailureCategory | None) -> str | None:
    return _CATEGORY_GUIDANCE.get(category) if category else None
