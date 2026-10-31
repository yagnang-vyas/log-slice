# LogSlice Roadmap

A statement of intent, not a commitment to dates.

**Legend:** ✅ Done · 🚧 In progress · 📋 Planned

**Current status:** v0.1 is implemented, and parts of v0.2 and v0.4 were delivered with it. See the details below.

## Versions

### v0.1: JSONL and nginx, ingest, SQL shell ✅

- ✅ JSONL parser with nested-field flattening
- ✅ nginx / Apache access log parser (common and combined)
- ✅ Automatic format detection
- ✅ Schema inference with type widening and timestamp normalization
- ✅ Streaming DuckDB ingest, in-memory or persistent
- ✅ Interactive SQL shell with multi-line input, history, `\schema`, `\stats`, `\export`
- ✅ Malformed-line handling and ingest statistics

**Done when:** a 1 GB JSONL benchmark ingests within the target time. ✅ The benchmark exists and runs (`tests/benchmarks`); results are in [PERFORMANCE.md](PERFORMANCE.md). No target time was ever defined, so none is claimed.

### v0.2: more formats, shortcuts 🚧

- ✅ gzip input and globs
- ✅ Shortcuts: `top errors`, `top endpoints`, `status codes`, `rate by`, `slowest`
- 📋 logfmt parser
- 📋 Custom regex parser (`--pattern`)
- 📋 `around <timestamp> <window>` and `--since` / `--until` ingest filters
- 📋 Multi-line records (stack traces joined to the preceding entry)

**Done when:** all shortcuts produce correct SQL in tests (✅ for the shortcuts above).

### v0.3: terminal UI 📋

- 📋 Full-screen UI with histogram and drill-down
- 📋 Snapshot tests for UI screens

### v0.4: exports and distribution 🚧

- ✅ Export to CSV, JSON, and JSON Lines
- ✅ Saved sessions via `--database` (reopen without re-ingesting)
- ✅ Dockerfile and wheel build in CI
- 📋 Parquet export
- 📋 Homebrew formula and a published PyPI release
- 📋 Published container image

## Future ideas (not scheduled)

- Live tailing
- Remote sources (S3, Kubernetes pod logs)
- Saved query packs
- Parallel parsing across processes, or an optional Go ingest path, for higher throughput
- Verified Windows and macOS shell history support

## How priorities are set

1. Correctness and safety of existing behavior.
2. Features that unblock the core use case described in the README.
3. Community requests with clear use cases (open an issue with the `enhancement` label).

## Tracking

Each item will be tracked as a GitHub issue and grouped into a milestone of the same name.

---

Maintained by **Yagnang Vyas** ([github.com/yagnang-vyas](https://github.com/yagnang-vyas)).
