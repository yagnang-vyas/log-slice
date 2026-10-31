"""Exception hierarchy. Each error carries the process exit code the CLI should use."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INPUT = 3


class LogSliceError(Exception):
    """Base class for all expected, user-facing errors."""

    exit_code = EXIT_FAILURE


class UsageError(LogSliceError):
    """Invalid command-line usage or configuration."""

    exit_code = EXIT_USAGE


class InputError(LogSliceError):
    """A missing, unreadable, or undetectable input file."""

    exit_code = EXIT_INPUT


class FormatDetectionError(InputError):
    """The log format could not be determined automatically."""


class IngestError(LogSliceError):
    """Loading data into DuckDB failed."""


class QueryError(LogSliceError):
    """A SQL statement or shortcut failed."""


class ShortcutError(QueryError):
    """A shortcut could not be translated for the current schema."""
