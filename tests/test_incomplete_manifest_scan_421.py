"""#421: a manifest the reader could not finish must not replace the table's stats."""

from __future__ import annotations

import pytest

from cryo.scanner.analyze import IncompleteManifestScan, manifest_read_failure
from cryo.scanner.go_scan import incomplete_scan_message


def test_go_payload_with_manifest_errors_is_not_a_successful_scan() -> None:
    message = incomplete_scan_message(
        {
            "ok": True,
            "manifest_read_errors": [
                "manifest file's 'format-version' metadata indicates version 2, "
                "but entry from manifest list indicates version 3"
            ],
        }
    )
    assert message is not None
    assert "previous stats were kept" in message
    assert incomplete_scan_message({"ok": True, "total_files": 10}) is None


def test_go_payload_keeps_the_engine_error_when_one_is_set() -> None:
    message = incomplete_scan_message(
        {
            "ok": False,
            "error": (
                "incomplete scan: 2 manifest read error(s); previous stats were kept. first: boom"
            ),
            "manifest_read_errors": ["boom", "second"],
        }
    )
    assert message is not None
    assert message.startswith("incomplete scan: 2")


def test_python_scanner_raises_instead_of_returning_short_counts() -> None:
    with pytest.raises(IncompleteManifestScan, match="previous stats were kept"):
        raise manifest_read_failure(["manifest list entry is version 3, file is version 2"])
