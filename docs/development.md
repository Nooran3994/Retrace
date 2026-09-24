# Development

This guide covers the codebase layout, how to run and test locally, and the
contribution workflow.

## Repository Layout

```
Retrace/
├── retrace/
│   ├── __init__.py              # version
│   ├── cli.py                   # argument parsing + all subcommands
│   ├── agent.py                 # persistent capture+detect loop
│   ├── watch.py                 # lighter ingest+detect loop
│   ├── webui.py                 # local-only HTTP server + HTML frontend
│   ├── db.py                    # SQLite schema, connect, insert, search, stats
│   ├── redact.py                # secret masking + entropy flagging
│   ├── detectors.py             # rule engine + built-in rules
│   ├── flight_recorder.py       # bash/zsh DEBUG-trap hook + spool ingest
│   ├── ps_transcript.py         # PowerShell transcript hook + parser
│   ├── models.py                # optional local-model analysis (Ollama/OpenAI)
│   ├── remote.py                # agentless SSH collection
│   └── collectors/
│       ├── __init__.py
│       ├── history.py           # bash/zsh/PowerShell history parsers
│       └── windows_events.py    # Get-WinEvent aggregator
├── scripts/
│   ├── install.sh               # one-command installer (Linux/macOS/WSL)
│   ├── install.ps1              # one-command installer (Windows)
│   ├── retrace-win.ps1          # Task Scheduler persistence helper
│   ├── retrace-run-agent.ps1    # Windows agent runner
│   ├── retrace-run-web.ps1      # Windows web runner
│   └── uninstall.sh             # removes hooks, units, tasks
├── systemd/
│   ├── retrace-agent.service    # systemd user unit
│   └── retrace-web.service      # systemd user unit
├── tests/
│   └── test_ps_parser.py        # PowerShell transcript parser tests
├── docs/                        # this documentation set
├── pyproject.toml
└── README.md
```

## Running Without Installing

```bash
# From the repo root — no pip, no venv
python3 -m retrace.cli stats
python3 -m retrace.cli search "curl"
python3 -m retrace.cli web
```

## Testing

```bash
# pytest (if installed)
python3 -m pytest tests/ -v

# unittest (stdlib, always available)
python3 -m unittest discover -s tests -v
```

Current coverage: PowerShell transcript parser (`tests/test_ps_parser.py`).
The redaction engine and detector engine are exercised manually via the CLI
and web UI; adding formal tests for both is a high-value contribution.

## Code Conventions

- **Stdlib only** — no third-party runtime dependencies. This is a hard
  constraint; it is what makes the one-command installer possible.
- **Docstrings on every module** — each module opens with a design contract
  (see `redact.py`, `remote.py`, `models.py` for the pattern).
- **Functions under ~30 lines** — helpers are extracted liberally.
- **Type hints** — `from __future__ import annotations`; use `|` unions.
- **Idempotent by default** — collectors dedup; hooks never duplicate;
  installers are safe to re-run.
- **Best-effort failure isolation** — every collector in the agent loop is
  wrapped in try/except so one failure never stops the loop.

## Adding a New Collector

1. Create `retrace/collectors/<name>.py` with a function that takes a
   `sqlite3.Connection` and returns a `dict` of counts (or `{"error": ...}`).
2. Redact every command before inserting — import `redact_line` and
   `is_high_entropy` from `..redact`.
3. Insert via `db.insert_command(...)` with the appropriate `source` value.
4. Wire it into the agent loop (`agent.py` step 1–5 pattern) and the watch
   loop (`watch.py`).
5. Document the source in `docs/data-model.md`.

## Adding a New Detector Rule

1. Add a rule dict to `BUILTIN_RULES` in `retrace/detectors.py`, or
2. Add it to `~/.config/retrace/detectors.json` (user rules merge over
   built-ins — see [Detectors](detectors.md)).

## Adding a New CLI Command

1. Add a `cmd_<name>` function in `retrace/cli.py`.
2. Register the subparser in `main()`.
3. Update `docs/usage.md` command table.

## Versioning

`__version__` lives in `retrace/__init__.py`. Bump it on feature additions.
The CLI exposes it via `retrace --version`.

## Commit Convention

```
feat(scope): summary        # new capability
fix(scope): summary         # bug fix
docs(scope): summary        # documentation
test(scope): summary        # tests
```

Examples from the project history:

```
feat(install): one-command installer + uninstaller
feat(win): Windows Task Scheduler persistence + event log fixes
feat(agent): persistent loop + local model analysis + systemd
feat(remote): agentless SSH collector
feat(phase3): local web UI + watch daemon + persisted alerts
feat(detectors): offline rule-based alert engine
feat(phase2): Windows Event Log collector + PS transcript hook
fix(flight): DEBUG-trap hook for interactive shells
feat(phase1): flight recorder + history ingest + redaction
```