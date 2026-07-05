"""Integration tests for custom maintenance script validation."""

from __future__ import annotations

from cryo.scripts.runner import CustomScriptRunner


def test_validate_script_blocks_os_system():
    """Scripts using os.system should be rejected."""
    runner = CustomScriptRunner()
    code = "import os\nos.system('rm -rf /')"

    is_valid, error = runner.validate_script(code)

    assert is_valid is False
    assert "Forbidden import" in error


def test_validate_script_blocks_subprocess():
    """Scripts importing subprocess should be rejected."""
    runner = CustomScriptRunner()
    code = "import subprocess\nsubprocess.run(['ls'])"

    is_valid, error = runner.validate_script(code)

    assert is_valid is False
    assert "subprocess" in error


def test_validate_script_blocks_eval_and_exec():
    """Dynamic code execution via eval/exec should be forbidden."""
    runner = CustomScriptRunner()

    for dangerous in ("eval('1+1')", "exec('print(1)')"):
        is_valid, error = runner.validate_script(dangerous)
        assert is_valid is False
        assert "Forbidden call:" in error


def test_validate_script_blocks_arbitrary_file_open():
    """Direct open() calls should be blocked to prevent file access."""
    runner = CustomScriptRunner()
    code = "with open('/etc/passwd') as f:\n    data = f.read()"

    is_valid, error = runner.validate_script(code)

    assert is_valid is False
    assert "open(" in error


def test_validate_script_accepts_safe_maintenance_logic():
    """Valid maintenance scripts using the Cryo SDK stub should pass validation."""
    runner = CustomScriptRunner()
    code = """
if table.small_files > 100:
    table.optimize(strategy='binpack')
table.compute_statistics(columns=['user_id'])
"""

    is_valid, error = runner.validate_script(code)

    assert is_valid is True
    assert error == ""


def test_validate_script_rejects_syntax_errors():
    """Scripts with syntax errors should fail validation."""
    runner = CustomScriptRunner()
    code = "if table.small_files >\n    table.optimize()"

    is_valid, error = runner.validate_script(code)

    assert is_valid is False
    assert "Syntax error" in error
