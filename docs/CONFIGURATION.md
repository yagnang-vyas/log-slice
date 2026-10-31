# Configuration

LogSlice needs no configuration. Settings are applied in this order, later steps winning:

1. Built-in defaults
2. The TOML config file
3. Environment variables
4. Command-line options

## Config file

Location: `--config FILE`, else `$LOGSLICE_CONFIG`, else `$XDG_CONFIG_HOME/logslice/config.toml` (default `~/.config/logslice/config.toml`). A file named with `--config` or `$LOGSLICE_CONFIG` must exist. The default location is optional.

```toml
[ingest]
batch_size = 50000     # rows per insert batch
flatten_depth = 3      # nesting levels flattened into dotted columns
keep_raw = false       # store every original line in _raw

[shell]
max_rows = 50          # rows displayed per result

[aliases]              # extra column names for shortcuts; tried before the built-in names
status = ["rc", "result_code"]
path = ["target"]
level = ["sev"]
message = ["detail"]
duration_ms = ["took_millis"]   # "duration" is accepted as the same role
ts = ["happened_at"]            # extra candidates for the event-time field
```

Invalid values (non-positive numbers, unknown alias roles, malformed TOML) stop the program with exit code 2 and a message naming the problem.

Duration columns ending in `_ms` are taken as milliseconds. `request_time`, `upstream_response_time`, and names ending in `_s`, `_sec`, or `_seconds` are taken as seconds. Anything else is assumed to be milliseconds.

## Environment variables

| Variable | Description |
|---|---|
| `LOGSLICE_CONFIG` | Path to an alternate config file |
| `LOGSLICE_MEMORY_LIMIT` | DuckDB memory limit, for example `4GB` |
| `LOGSLICE_THREADS` | DuckDB thread count |
| `LOGSLICE_HISTORY` | Shell history file (default `~/.logslice_history`) |
| `XDG_CONFIG_HOME` | Base directory for the default config location |

## Command-line overrides

`--batch-size`, `--max-rows`, `--memory-limit`, `--threads`, and `--keep-raw` override the matching settings. See the [CLI reference](CLI_REFERENCE.md).
