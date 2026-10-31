# Contributing to LogSlice

Thank you for your interest in improving LogSlice, a command-line tool for querying messy log files with SQL. This guide explains how to propose changes.

> LogSlice v0.1 is implemented. Bug reports, new parsers, new shortcuts, and documentation fixes are all welcome.

## Ground rules

- Be respectful and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- For anything larger than a small fix, open an issue first to discuss the approach.
- Keep pull requests focused on one change.
- Do not include secrets, credentials, or real personal or customer data anywhere in the repository, including tests and fixtures.

## Project-specific guidelines

- New parsers need small fixture files in `tests/fixtures/` covering normal, malformed, and multi-line entries. Never commit real production logs.
- Ingest changes must include before/after numbers from `python tests/benchmarks/run_benchmark.py` on the same machine in the pull request.
- Shortcuts must print the SQL they execute.

## Getting started

```bash
git clone https://github.com/yagnang-vyas/log-slice.git
cd log-slice
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
logslice --help
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full development guide.

## Branch naming

| Prefix | Use for |
|---|---|
| `feat/` | New functionality |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `refactor/` | Code changes without behavior change |
| `test/` | Tests only |
| `chore/` | Tooling, dependencies, CI |

## Commit messages

LogSlice uses [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <short summary>

<optional body explaining what and why>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `chore`, `ci`, `build`.

Suggested scopes: `cli`, `io`, `detect`, `parsers`, `schema`, `ingest`, `shortcuts`, `shell`, `export`, `docs`, `ci`.

Examples:

```text
feat(cli): add support for a new input format
fix(io): handle empty input without crashing
docs: clarify installation steps
```

Commit messages must not contain `Co-authored-by` trailers for AI tools or any AI attribution.

## Before opening a pull request

Run the same checks as CI:

```bash
ruff format --check .
ruff check .
mypy
pytest
```

Then confirm:

- [ ] Tests cover the change (positive and negative cases where relevant).
- [ ] Documentation is updated (README, `docs/`, or inline comments).
- [ ] No secrets, credentials, or real personal data are included.
- [ ] Commits follow Conventional Commits.

## Pull request process

1. Fork the repository and create a branch from `main`.
2. Make your change with tests.
3. Open a pull request describing the problem, the solution, and how you tested it.
4. The maintainer reviews the change. Please respond to feedback; small follow-up commits are fine.
5. The maintainer merges once checks pass and the review is approved.

## Reporting bugs

Open an issue with: what you expected, what happened, steps to reproduce, and your environment (OS, runtime versions). For security problems, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Licensing of contributions

By contributing, you agree that your contributions are licensed under the project's license (see [LICENSE](LICENSE)). You keep authorship of your own commits.

## Maintainer

LogSlice is maintained by **Yagnang Vyas** ([https://github.com/yagnang-vyas](https://github.com/yagnang-vyas)).
