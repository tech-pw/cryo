"""Custom maintenance script runner.

Allows power users to upload Python scripts that run as maintenance operations.
Scripts run in sandboxed Docker containers with resource limits.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class CustomScriptConfig:
    """Configuration for a custom maintenance script."""

    script_name: str
    script_code: str  # Python code
    table_id: UUID
    timeout_seconds: int = 300
    memory_limit_mb: int = 512
    cpu_limit: float = 1.0


class CustomScriptRunner:
    """Runs custom maintenance scripts in sandboxed environment."""

    def __init__(
        self,
        docker_image: str = "python:3.12-slim",
    ):
        self.docker_image = docker_image

    def validate_script(self, script_code: str) -> tuple[bool, str]:
        """
        Validate script for security issues.

        Args:
            script_code: Python code to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Basic security checks
        dangerous_imports = [
            "os.system",
            "subprocess",
            "eval",
            "exec",
            "__import__",
            "open(",  # Prevent arbitrary file access
        ]

        for dangerous in dangerous_imports:
            if dangerous in script_code:
                return False, f"Forbidden operation: {dangerous}"

        # Check script compiles
        try:
            compile(script_code, "<string>", "exec")
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

        return True, ""

    def run_script(
        self,
        config: CustomScriptConfig,
        table_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run a custom maintenance script in a sandboxed environment.

        Args:
            config: Script configuration
            table_metadata: Table metadata to pass to script

        Returns:
            Script execution result
        """
        # Validate script
        is_valid, error = self.validate_script(config.script_code)
        if not is_valid:
            return {
                "status": "failed",
                "error": error,
            }

        # Create wrapper script that injects SDK and runs user code
        wrapper_code = f"""
import json
import sys

# Cryo SDK stub (production would provide full SDK)
class Table:
    def __init__(self, metadata):
        self.id = metadata['id']
        self.name = metadata['name']
        self.size_gb = metadata['size_gb']
        self.small_files = metadata['small_files']
    
    def optimize(self, strategy='binpack', columns=None):
        print(f"[CRYO] optimize table={{self.id}} strategy={{strategy}} columns={{columns}}")
    
    def expire_snapshots(self, retention_days=7):
        print(f"[CRYO] expire_snapshots table={{self.id}} retention_days={{retention_days}}")
    
    def compute_statistics(self, columns=None):
        print(f"[CRYO] compute_statistics table={{self.id}} columns={{columns}}")

# Load table metadata
table = Table({json.dumps(table_metadata)})

# Run user code
try:
{self._indent_code(config.script_code, 4)}
    print("[CRYO] Script completed successfully")
except Exception as e:
    print(f"[CRYO] Script failed: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

        # Write wrapper script to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(wrapper_code)
            script_path = f.name

        try:
            # Run script in Docker container with resource limits
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",  # No network access
                "--memory",
                f"{config.memory_limit_mb}m",
                "--cpus",
                str(config.cpu_limit),
                "--read-only",  # Read-only filesystem
                "-v",
                f"{script_path}:/script.py:ro",
                self.docker_image,
                "python",
                "/script.py",
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
            )

            # Parse output for Cryo commands
            operations = []
            for line in result.stdout.splitlines():
                if line.startswith("[CRYO]"):
                    operations.append(line[7:].strip())  # Remove "[CRYO] " prefix

            return {
                "status": "completed" if result.returncode == 0 else "failed",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "operations": operations,  # Parsed Cryo operations to execute
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "error": f"Script exceeded timeout of {config.timeout_seconds}s",
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e),
            }
        finally:
            # Clean up temp file
            Path(script_path).unlink(missing_ok=True)

    def _indent_code(self, code: str, spaces: int) -> str:
        """Indent code by given number of spaces."""
        indent = " " * spaces
        return "\n".join(indent + line for line in code.splitlines())


# Example custom script:
EXAMPLE_SCRIPT = """
# Custom maintenance logic
if table.size_gb > 1000:
    # Large tables get z-order on high-cardinality columns
    table.optimize(strategy='zorder', columns=['user_id', 'event_date'])
else:
    # Small tables get simple bin-pack
    table.optimize(strategy='binpack')

# Always compute stats after optimization
table.compute_statistics(columns=['user_id', 'event_date', 'event_type'])

# Expire old snapshots for tables with many small files
if table.small_files > 1000:
    table.expire_snapshots(retention_days=3)
"""
