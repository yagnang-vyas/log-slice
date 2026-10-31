from pathlib import Path

import pytest

from logslice.config import Config, load_config
from logslice.errors import UsageError


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("LOGSLICE_CONFIG", "LOGSLICE_MEMORY_LIMIT", "LOGSLICE_THREADS"):
        monkeypatch.delenv(var, raising=False)
    assert load_config() == Config()


def test_file_values(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        """
[ingest]
batch_size = 1000
flatten_depth = 2
keep_raw = true

[shell]
max_rows = 7

[aliases]
duration_ms = ["took_millis"]
path = ["target"]
ts = ["happened"]
"""
    )
    config = load_config(path)
    assert (config.batch_size, config.flatten_depth, config.keep_raw, config.max_rows) == (
        1000,
        2,
        True,
        7,
    )
    assert config.aliases == {
        "duration": ["took_millis"],
        "path": ["target"],
        "ts": ["happened"],
    }


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("")
    monkeypatch.setenv("LOGSLICE_CONFIG", str(path))
    monkeypatch.setenv("LOGSLICE_MEMORY_LIMIT", "2GB")
    monkeypatch.setenv("LOGSLICE_THREADS", "3")
    config = load_config()
    assert config.memory_limit == "2GB" and config.threads == 3


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[ingest]\nbatch_size = 0", "batch_size"),
        ("[ingest]\nbatch_size = 'x'", "batch_size"),
        ("[aliases]\nnonsense = ['a']", "unknown alias role"),
        ("[aliases]\nlevel = 'a'", "list of strings"),
        ("not = [valid", "cannot read config"),
    ],
)
def test_invalid_config(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "c.toml"
    path.write_text(content)
    with pytest.raises(UsageError, match=message):
        load_config(path)


def test_missing_explicit_config(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_invalid_threads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("LOGSLICE_CONFIG", raising=False)
    monkeypatch.setenv("LOGSLICE_THREADS", "many")
    with pytest.raises(UsageError, match="LOGSLICE_THREADS"):
        load_config()
