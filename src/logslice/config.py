"""Configuration: defaults, optional TOML file, and environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from logslice.errors import UsageError

DEFAULT_BATCH_SIZE = 50_000
DEFAULT_FLATTEN_DEPTH = 3
DEFAULT_MAX_ROWS = 50

ROLE_NAMES = ("ts", "level", "status", "path", "message", "duration")
_ROLE_KEY_ALIASES = {"duration_ms": "duration"}


@dataclass
class Config:
    """Runtime settings. Command-line options override file values, which override defaults."""

    batch_size: int = DEFAULT_BATCH_SIZE
    flatten_depth: int = DEFAULT_FLATTEN_DEPTH
    keep_raw: bool = False
    max_rows: int = DEFAULT_MAX_ROWS
    memory_limit: str | None = None
    threads: int | None = None
    aliases: dict[str, list[str]] = field(default_factory=dict)


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "logslice" / "config.toml"


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise UsageError(f"config: {name} must be a positive integer, got {value!r}")
    return value


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration from `path`, `$LOGSLICE_CONFIG`, or the default location.

    An explicitly requested file (argument or environment variable) must exist;
    the default location is optional.
    """
    explicit = path or os.environ.get("LOGSLICE_CONFIG")
    cfg_path = Path(explicit) if explicit else default_config_path()
    config = Config()
    if cfg_path.is_file():
        try:
            data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise UsageError(f"cannot read config file {cfg_path}: {exc}") from exc
        _apply_file(config, data)
    elif explicit:
        raise UsageError(f"config file not found: {cfg_path}")
    _apply_env(config)
    return config


def _apply_file(config: Config, data: dict[str, Any]) -> None:
    ingest = data.get("ingest", {})
    if "batch_size" in ingest:
        config.batch_size = _positive_int(ingest["batch_size"], "ingest.batch_size")
    if "flatten_depth" in ingest:
        config.flatten_depth = _positive_int(ingest["flatten_depth"], "ingest.flatten_depth")
    if "keep_raw" in ingest:
        config.keep_raw = bool(ingest["keep_raw"])
    shell = data.get("shell", {})
    if "max_rows" in shell:
        config.max_rows = _positive_int(shell["max_rows"], "shell.max_rows")
    for key, names in data.get("aliases", {}).items():
        role = _ROLE_KEY_ALIASES.get(key, key)
        if role not in ROLE_NAMES:
            raise UsageError(
                f"config: unknown alias role {key!r}; expected one of {', '.join(ROLE_NAMES)}"
            )
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise UsageError(f"config: aliases.{key} must be a list of strings")
        config.aliases[role] = list(names)


def _apply_env(config: Config) -> None:
    limit = os.environ.get("LOGSLICE_MEMORY_LIMIT")
    if limit:
        config.memory_limit = limit
    threads = os.environ.get("LOGSLICE_THREADS")
    if threads:
        try:
            config.threads = _positive_int(int(threads), "LOGSLICE_THREADS")
        except ValueError as exc:
            raise UsageError(f"LOGSLICE_THREADS must be an integer, got {threads!r}") from exc
