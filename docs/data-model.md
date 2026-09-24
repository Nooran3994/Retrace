# Data Model

Retrace stores everything in a single local SQLite database. The schema is
**append-only by design** — rows are immutable once written, which keeps
replay timelines honest.

## Database Location

| Platform | Path |
|----------|------|
| Linux / WSL2 | `~/.local/share/retrace/rec.db` (or `$XDG_DATA_HOME/retrace/rec.db`) |
| Windows native | `%LOCALAPPDATA%\retrace\rec.db` |

The parent directory is chmod 700; the DB is created with WAL journal mode.

## Tables

### `commands`

The core table — one row per captured command or event.

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID hex, first 12 chars |
| `ts` | REAL | Epoch seconds (UTC) |
| `source` | TEXT | `history` \| `flight` \| `import` \| `remote` \| `ps_transcript` \| `win_security` \| `win_system` \| `win_application` \| `win_powershell` |
| `shell` | TEXT | `bash` \| `zsh` \| `powershell` \| `cmd` |
| `command` | TEXT | **Redacted** command text |
| `cwd` | TEXT | Working directory |
| `exit_code` | INTEGER | Process exit code |
| `duration_ms` | INTEGER | Command duration |
| `session_id` | TEXT | Session identifier |
| `host` | TEXT | Machine name |
| `git_repo` | TEXT | Repo name (git-aware capture) |
| `git_branch` | TEXT | Branch at capture time |
| `git_dirty` | INTEGER | 1 if uncommitted changes |
| `entropy` | INTEGER | 1 if high-entropy token flagged |
| `raw_output` | TEXT | **Redacted** captured output (flight only) |

Indexes: `ts`, `source`, `(git_repo, git_branch)`.

### `alerts`

One row per fired detection.

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID hex, first 12 chars |
| `ts` | REAL | Alert creation time |
| `rule` | TEXT | Rule name (or `model-insight`) |
| `severity` | TEXT | `info` \| `medium` \| `high` \| `critical` |
| `message` | TEXT | Human-readable message |
| `count` | INTEGER | Occurrence count |
| `first_ts` | REAL | First matching event |
| `last_ts` | REAL | Last matching event |
| `samples` | TEXT | JSON array of sample commands |
| `acked` | INTEGER | 0 = unacked, 1 = acknowledged |

Indexes: `ts`, `severity`.

### `ps_ingested` (transcript marker table)

| Column | Type | Notes |
|--------|------|-------|
| `path` | TEXT PK | Transcript file path |
| `ts` | REAL | When it was processed |

Used to make PowerShell transcript ingestion idempotent.

## Sources

| Source | Origin | Collector |
|--------|--------|-----------|
| `history` | `~/.bash_history`, `~/.zsh_history`, PSReadLine history | `collectors/history.py` |
| `flight` | Live DEBUG-trap spool | `flight_recorder.py` |
| `ps_transcript` | `Start-Transcript` session logs | `ps_transcript.py` |
| `win_security` / `win_system` / `win_application` / `win_powershell` | Windows Event Log channels | `collectors/windows_events.py` |
| `remote` | Agentless SSH collection from other devices | `remote.py` |

## Query Patterns

```bash
# Total + breakdown
retrace stats

# Search
retrace search "kubectl" --limit 50

# Raw SQL (read-only)
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect("~/.local/share/retrace/rec.db")
print(conn.execute("SELECT source, COUNT(*) FROM commands GROUP BY source").fetchall())
EOF
```

## Export

`retrace export` dumps the full `commands` table:

```bash
retrace export --format jsonl --output out.jsonl
retrace export --format csv    --output out.csv
retrace export --format json   --output out.json
```

Export is explicit and user-initiated — the only path by which data leaves
the machine (aside from opt-in remote/model features).

## Schema Evolution

The schema is created idempotently via `CREATE TABLE IF NOT EXISTS` on every
`connect()`. New columns are additive; migrations are backward-compatible.
The `ps_ingested` marker table is created lazily by the transcript collector.