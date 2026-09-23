from __future__ import annotations

import json

import pytest

from cryo.scanner.go_scan import _parse_engine_payload


def _parse(stdout: str) -> dict[str, object]:
    return _parse_engine_payload(stdout, command="scan-stats", returncode=0, stderr="")


def test_plain_single_line_response_is_parsed() -> None:
    assert _parse('{"ok": true, "total_files": 3}') == {"ok": True, "total_files": 3}


@pytest.mark.parametrize("sep", ["\u0085", "\u2028", "\u2029"])
def test_unicode_line_boundaries_inside_a_string_value_do_not_break_parsing(sep: str) -> None:
    """The scanner samples real row data, which carries these characters.

    Go's json encoder leaves them unescaped because they are all >= U+0020, so
    they reach us literally inside a one-line JSON document. Parsing must not
    treat them as line breaks.
    """
    payload = {"ok": True, "total_files": 1, "sample": f"before{sep}after"}
    stdout = json.dumps(payload, ensure_ascii=False)

    assert len(stdout.splitlines()) > 1, "precondition: splitlines() sees a break here"
    assert _parse(stdout) == payload


def test_progress_lines_before_the_response_are_skipped() -> None:
    stdout = 'progress: 10%\n{"ok": true, "total_files": 7}'
    assert _parse(stdout) == {"ok": True, "total_files": 7}


def test_last_json_object_wins_when_several_are_emitted() -> None:
    stdout = '{"ok": false, "stage": "early"}\n{"ok": true, "stage": "final"}'
    assert _parse(stdout)["stage"] == "final"


def test_unparseable_output_reports_exit_code_and_stderr() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        _parse_engine_payload(
            "segmentation fault", command="scan-stats", returncode=139, stderr="boom"
        )

    message = str(excinfo.value)
    assert "exit=139" in message
    assert "boom" in message
