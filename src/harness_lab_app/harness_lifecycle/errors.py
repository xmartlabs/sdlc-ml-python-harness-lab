"""Shared exit codes and the domain error for the harness lifecycle CLI.

The codes mirror the plan's standard table (§9.1) so every subcommand surfaces a
consistent, scriptable exit status.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_INVALID = 2  # invalid arguments or malformed manifest
EXIT_PRECONDITION = 3  # failed precondition or dirty tree
EXIT_INCOMPATIBLE = 4  # sync protocol / platform incompatibility
EXIT_CONFLICT = 5  # conflict, drift, or forbidden-path change
EXIT_GATE_FAILED = 6  # regression hard gate failed
EXIT_MISSING = 7  # missing tag, release asset, external tool, network, or auth
EXIT_CANCELLED = 130


class LifecycleError(Exception):
    """Raised on invalid input or a failed precondition; carries an exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_INVALID) -> None:
        super().__init__(message)
        self.exit_code = exit_code
