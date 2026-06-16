"""Scout engine exception hierarchy and exit code contract.

Each ScoutError subclass maps to a specific exit code. The CLI
layer converts exceptions to exit codes + stderr messages.
Scout-app decodes exit codes back into specific Swift error cases.
"""

from __future__ import annotations


class ScoutError(Exception):
    """Base for all scout-specific errors."""

    exit_code = 1


class ConfigError(ScoutError):
    """Invalid or missing configuration."""

    exit_code = 10


class DataDirError(ScoutError):
    """SCOUT_DATA_DIR unset, invalid, or inaccessible."""

    exit_code = 11


class SchemaVersionMismatch(ScoutError):
    """Data dir schema version does not match engine expectation."""

    exit_code = 12

    def __init__(self, have: int, want: int) -> None:
        super().__init__(
            f"Data dir at schema v{have}; engine expects v{want}. "
            f"Run: scoutctl migrate data-dir --from {have} --to {want}"
        )
        self.have = have
        self.want = want


class KBError(ScoutError):
    """Knowledge-base query failure."""

    exit_code = 20


class KBSchemaError(KBError):
    """Raised when the knowledge-base schema YAML is missing or malformed."""


class ActionItemError(ScoutError):
    """Action-item operation failure (no-match, ambiguous, write error)."""

    exit_code = 21


class ExternalProcessError(ScoutError):
    """Subprocess (git, claude, launchctl) failed."""

    exit_code = 30


class ContractViolation(ScoutError):
    """Manifest missing a required feature flag."""

    exit_code = 40
