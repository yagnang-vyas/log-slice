# CLI Reference

```text
logslice [FILE ...] [options]
```

`FILE` is a path or glob pattern (quote globs so your shell does not expand them: `'logs/*.jsonl'`). With no files, `--database` must point to an existing database.

## Options

| Option | Description |
|---|---|
| `-f, --format FORMAT` | `auto` (default), `jsonl`, `nginx`. Aliases: `json`, `ndjson`, `apache`, `combined` |
| `-d, --database PATH` | Use a persistent DuckDB file instead of memory. With files, loads into it. Without files, reopens it |
| `--append` | Add rows to an existing `logs` table in `--database` |
| `--overwrite` | Replace an existing `logs` table in `--database` |
| `-q, --query SQL` | Run one SQL statement or shortcut, print the result, and exit |
| `-o, --out FILE` | With `--query`, write the result to `.csv`, `.json`, or `.jsonl` instead of printing |
| `--schema` | Print columns, types, null rates, and examples, then exit |
| `--stats` | Print ingestion statistics, then exit |
| `--keep-raw` | Store each original line in `_raw` (malformed lines are always stored) |
| `--drop-malformed` | Skip unparseable lines instead of keeping them as rows |
| `--batch-size N` | Rows per insert batch (default 50,000) |
| `--max-rows N` | Rows to display per result (default 50) |
| `--memory-limit SIZE` | DuckDB memory limit, for example `4GB` |
| `--threads N` | DuckDB thread count |
| `--config FILE` | TOML configuration file |
| `--no-progress` | Disable the progress line (shown only on a terminal) |
| `--quiet` | Suppress the ingest summary and warnings |
| `--version` | Print the LogSlice and DuckDB versions |
| `-h, --help` | Show help |

## Output streams

Query results go to standard output. The ingest summary, warnings, the SQL of shortcuts, and export messages go to standard error, so `logslice access.log -q "status codes" > codes.txt` captures only the table.

If standard input is not a terminal and no `--query` is given, the shell reads statements from standard input and prints results, which makes scripted sessions possible:

```bash
printf 'status codes\n\\export out.csv\n' | logslice access.log --quiet
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A query, shortcut, or ingest step failed, including SQL errors in `--query` |
| 2 | Usage error: bad option, missing input, invalid config, `--out` without `--query` |
| 3 | Input error: file or database not found, unreadable file, format could not be detected |
| 130 | Interrupted with Ctrl-C |

Errors print as `logslice: error: <message>` without a traceback.

## Shell

End SQL statements with `;`. They may span lines. Shortcuts and backslash commands run as soon as you press Enter. Press Ctrl-C to discard a half-typed statement and Ctrl-D to leave.

| Command | Description |
|---|---|
| `\help` | Show help |
| `\schema`, `\fields`, `fields` | Columns with type, null rate, and an example value |
| `\stats` | Ingestion statistics, including malformed-line samples |
| `\shortcuts` | List shortcuts |
| `\export FILE [QUERY]` | Write the last query (or `QUERY`, SQL or shortcut) to `FILE` |
| `\limit N` | Rows displayed per result |
| `\history` | The last 20 inputs |
| `\cancel`, `\c` | Discard the statement being typed |
| `\quit`, `\q`, `exit`, `quit` | Leave the shell |

History is saved to `~/.logslice_history` (override with `LOGSLICE_HISTORY`) where the Python `readline` module is available. It is not available on stock Windows Python, where the shell works without history.

## Shortcuts

| Shortcut | Result columns |
|---|---|
| `top errors [N] [where COND]` | `error`, `n`, `first_seen`, `last_seen` |
| `top endpoints [N] [where COND]` | `path`, `n`, `pct`, `errors_5xx` |
| `status codes [where COND]` | `status`, `n`, `pct` |
| `rate by <N><s\|m\|h\|d> [where COND]` | `bucket`, `n` |
| `rate COND by <N><unit>` | `bucket`, `n` |
| `slowest [N] [endpoints \| by COL] [where COND]` | the grouping column, `n`, `avg_ms`, `p50_ms`, `p95_ms`, `p99_ms`, `max_ms` |

`N` defaults to 10. Conditions are SQL and may use the role names `status`, `level`, `path`, `message`, and `duration`, which are replaced by the real columns.

**How errors are identified:** a row is an error if its level is one of ERROR, ERR, FATAL, CRITICAL, CRIT, PANIC, SEVERE, EMERG, or ALERT (case-insensitive), or its status is 500 or above. If no message column exists (as in nginx logs), the error text is the status and the path with digits masked.

**Role names tried (first match wins; your aliases go first):**

| Role | Columns |
|---|---|
| status | `status`, `status_code`, `http.response.status_code`, `http.status_code`, `http.status`, `http.statuscode`, `response.status`, `response.statuscode`, `res.statuscode`, `statuscode`, `http_status` |
| path | `path`, `url.path`, `http.request.path`, `http.path`, `http.url`, `req.url`, `request.url`, `request.path`, `request_path`, `uri`, `request_uri`, `url`, `endpoint`, `route` |
| level | `level`, `severity`, `log.level`, `loglevel`, `levelname`, `severity_text`, `lvl` |
| message | `message`, `msg`, `error.message`, `error`, `log`, `text` |
| duration | `duration_ms`, `latency_ms`, `response_time_ms`, `elapsed_ms`, `took_ms`, `request_time`, `upstream_response_time`, `duration_s`, `duration`, `latency`, `response_time`, `elapsed`, `took` |

If a shortcut cannot find a column it says so and suggests an alias or plain SQL.
