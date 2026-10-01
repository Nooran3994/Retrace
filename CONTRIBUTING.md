# Contributing to Retrace

Thanks for your interest in Retrace. This project is small, local-first, and
deterministic by design. Before you open a PR, please read this document —
it will save both of us time.

## Ground rules

1. **Stdlib only.** The core must stay Python 3.9+ **stdlib-only**. No pip
   dependencies, no venv, no `requirements.txt`. If your change needs a third-
   party package, propose it in an issue first — it will likely be rejected
   unless there is a compelling reason.
2. **Local-first.** Nothing may phone home. No telemetry, no CDN, no network
   calls unless the user explicitly triggers them (export, remote collect).
3. **Deterministic over agentic.** The capture/detect loop never depends on a
   model. Model analysis is opt-in and advisory only.
4. **Redaction-first.** Any new data source must pass through the redaction
   engine before storage. Secrets are masked at the boundary, never stored raw.
5. **Append-only.** Command/alert rows are immutable once written. Don't add
   UPDATE/DELETE paths to the core tables.

## Development setup

```bash
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
python3 -m retrace.cli stats      # creates DB + config dirs
python3 -m retrace.cli hook install
python3 -m retrace.cli ingest
python3 -m retrace.cli web        # local UI at http://127.0.0.1:8765
```

No virtualenv, no install step required for development. If you use the
entry-points, `pip install -e .` is fine but optional.

## Running tests

```bash
python3 -m pytest tests/ -v
# or, without pytest:
python3 tests/test_ps_parser.py
```

New features must ship with tests. The project currently has one test file
(`tests/test_ps_parser.py`); add sibling files per module as needed.

## Commit conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/).
Allowed types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`,
`security`.

- Subject ≤ 72 chars, imperative mood, no trailing period.
- Body explains **why**, not just what.
- One logical change per commit.
- No secrets, no personal data, no internal hostnames in diffs or messages.

Example:

```
feat(analytics): add hourly command frequency endpoint

Aggregates commands per hour for the dashboard time-series chart.
Computed on the fly from the append-only table — no schema change,
no migration, no risk to existing rows.
```

## Pull request checklist

- [ ] Tests pass (`python3 -m pytest tests/ -v`)
- [ ] Stdlib-only core preserved (no new pip deps)
- [ ] No new network calls unless user-triggered
- [ ] Redaction applied to any new data source
- [ ] Docs updated if user-facing behavior changed (`docs/`, README)
- [ ] Commit messages follow Conventional Commits

## Reporting issues

- **Bugs:** include the exact command that failed, Python version, OS, and
  the relevant output (redact any secrets first).
- **Security vulnerabilities:** do NOT open a public issue. Email the
  maintainer or open a private report — see `SECURITY.md`.
- **Feature requests:** explain the use case, not just the feature. Retrace
  is deliberately small; we'd rather reject a feature than bloat the core.

## Code of conduct

Be respectful. This is a small open-source project maintained by one person.
Assume good faith, give constructive feedback, and remember that maintainers
have limited time.

## License

By contributing you agree that your contributions are licensed under the
project's MIT license (see `LICENSE`).