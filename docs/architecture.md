# Architecture

Retrace is a layered, local-first system. Every layer is a Python module under
`retrace/`, uses only the standard library, and is independently testable.

## Data Flow

```
capture (history / flight / ps-transcript / win-events / remote SSH)
   │
   ▼
redact (secrets masked BEFORE storage; original discarded)
   │
   ▼
store (append-only SQLite, WAL mode, perms locked to user)
   │
   ▼
detect (10 offline deterministic rules → persisted alerts)
   │
   ▼
view (localhost-only web UI @ 127.0.0.1:8765)
   │
   ▼
export (jsonl / csv / json — explicit, user-initiated only)
```

The **agent loop** (`retrace agent`) runs this pipeline on an interval
(default 60 s) and is the production entry point under systemd or Task
Scheduler. `retrace watch` is a lighter variant without model analysis.

## Module Map

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `cli.py` | 346 | Argument parsing, command dispatch, all subcommands |
| `webui.py` | 367 | Local-only HTTP server + self-contained HTML frontend |
| `models.py` | 351 | Optional local-model analysis (Ollama / OpenAI-compatible) |
| `remote.py` | 301 | Agentless SSH collection from remote devices |
| `detectors.py` | 260 | Deterministic rule engine + built-in rule set |
| `agent.py` | 201 | Persistent capture+detect loop with health state |
| `db.py` | 154 | SQLite schema, connection, insert, search, stats |
| `ps_transcript.py` | 130 | PowerShell transcript hook + parser |
| `watch.py` | 119 | Periodic ingest+detect loop (no model) |
| `flight_recorder.py` | 103 | Bash/zsh DEBUG-trap hook + spool ingest |
| `redact.py` | 98 | Secret masking engine + entropy flagging |
| `collectors/history.py` | — | Bash/zsh/PowerShell history parsers |
| `collectors/windows_events.py` | — | `Get-WinEvent` aggregator (native + WSL interop) |

## Design Principles

### 1. The logs are already there
Retrace's collectors read what shells and Windows already write:
- `~/.bash_history` (with optional `#<epoch>` timestamps)
- `~/.zsh_history` (extended `: ts:dur;cmd` format)
- PowerShell PSReadLine `ConsoleHost_history.txt`
- Windows Event Log channels via `Get-WinEvent`
- Remote devices over SSH (see [Remote Collection](remote-collection.md))

Nothing is injected into the system except the optional flight-recorder hook,
which captures what the shell was already doing — with exit codes, duration,
cwd, and git context.

### 2. Redaction-first
Every captured line passes through `redact.py` before storage. The engine:

- Masks whole-line assignments: `export TOKEN=...` → `export TOKEN=[REDACTED]`
- Masks known secret shapes: GitHub PATs (`ghp_`, `github_pat_`, `gho_`),
  Slack tokens (`xox*`), AWS keys (`AKIA...`), OpenAI keys (`sk-...`), JWTs
- Masks `key=value` pairs where the key looks secret-ish
- Masks `Bearer <token>` sequences
- Flags high-entropy tokens (Shannon entropy ≥ 3.5) — the flag is stored, the
  text is not

The original text is **discarded, not reversible**. See
[Security](security.md) for the full threat model.

### 3. Offline by default
- Zero outbound network calls in the capture/detect/store/view path
- The web UI binds to `127.0.0.1` only — never `0.0.0.0`
- No CDN assets, no telemetry, no analytics
- Remote collection is opt-in and SSH-encrypted
- Model analysis is off until explicitly enabled

### 4. Deterministic over agentic
Detection is a pure function over captured rows: regex rules with severity,
time windows, and thresholds. No LLM, no network, no external calls. The model
layer (if enabled) runs **after** deterministic detection, sees **only redacted
text**, and its output is advisory — it can insert insight rows into the alerts
table but can never modify or delete command rows.

### 5. Append-only storage
Rows in the `commands` table are immutable once written. The schema uses WAL
journal mode and locks file permissions to the current user. Replaying a
timeline is therefore honest — nothing is silently rewritten.

## The Agent Loop

```
per cycle (default 60 s):
  1. ingest shell history           → dedup against existing rows
  2. ingest flight spool            → idempotent by (ts, cmd)
  3. ingest PowerShell transcripts  → tracks processed files
  4. collect Windows events         → best-effort, skips inaccessible channels
  5. collect remote hosts           → opt-in, only if hosts are registered
  6. run deterministic detection    → persist alerts
  7. (opt-in) local model analysis  → advisory insights only
  8. write state.json               → health + last-cycle summary for the UI
```

Every step is wrapped so a failure in one collector never breaks the loop.
The agent is systemd-friendly: it restarts on crash, skips a cycle if it
overruns the interval, and reports health via `state.json`.

## Web UI

`retrace web` serves a single self-contained HTML page from memory. Routes:

| Route | Purpose |
|-------|---------|
| `GET /` | The UI |
| `GET /api/stats` | Aggregate stats |
| `GET /api/records?limit=&q=` | Recent records / search |
| `GET /api/alerts?since=` | Persisted alerts |
| `POST /api/detect` | Run detectors now, persist, return alerts |
| `POST /api/alerts/ack` | Acknowledge an alert `{id}` |

The frontend auto-refreshes every 15 s and calls only local endpoints.

## Persistence

- **Linux / WSL2 with systemd**: user units `retrace-agent.service` and
  `retrace-web.service` (see [Persistence](persistence.md))
- **Windows native / WSL2 without systemd**: Task Scheduler via
  `scripts/retrace-win.ps1`

## Tests

`tests/test_ps_parser.py` covers the PowerShell transcript parser. Run with:

```bash
python3 -m pytest tests/ -v        # or: python3 -m unittest discover -s tests
```