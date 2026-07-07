from __future__ import annotations

from unittest.mock import MagicMock

from cryo.cli import ScannerRunner, create_parser, run_scanner
from cryo.scanner.worker import ScanWorkResult


def test_create_parser_registers_scanner_command() -> None:
    args = create_parser().parse_args(["scanner", "--poll-interval", "7"])
    assert args.command == "scanner"
    assert args.poll_interval == 7


def test_scanner_runner_processes_next_queue_item() -> None:
    worker = MagicMock()
    worker.process_next.return_value = ScanWorkResult(
        run_id="597bc2e7-2cf9-4779-a1da-c6db027aa990",
        status="running",
    )
    runner = ScannerRunner(interval=5)
    runner._worker = worker

    runner._tick()

    worker.process_next.assert_called_once_with()


def test_run_scanner_starts_scanner_runner(monkeypatch) -> None:
    runner = MagicMock()
    run_with_signal = MagicMock()
    monkeypatch.setattr("cryo.cli.ScannerRunner", lambda interval: runner)
    monkeypatch.setattr("cryo.cli._run_with_signal", run_with_signal)

    run_scanner(create_parser().parse_args(["scanner", "--poll-interval", "9"]))

    run_with_signal.assert_called_once_with(runner, service_name="scanner")
