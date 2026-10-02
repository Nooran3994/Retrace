# Persistence & Data Model

Retrace stores everything in a local **SQLite** database — append-only, WAL mode, perms-locked.

## Storage engine

- **SQLite** (stdlib `sqlite3`) — no external database server
- **WAL mode** — concurrent reads don't block the collector
- **Append-only core tables** — command and alert rows are immutable once written
- **File permissions locked** — the DB is not world-readable

## Core tables

| Table | Purpose |
|---|---|
| `commands` | Recorded command events (timestamp, source, shell, command text, cwd, exit code, duration) |
| `alerts` | Detector hits (rule, severity, matched command, acknowledged flag) |
| `hosts` | Hosts/sources contributing records |
| `remotes` | Registered SSH hosts |
| `summary_*` | Materialized rollup tables (hourly/daily aggregates) for fast dashboard loads |

## Command row shape

```json
{
  "id": 12345,
  "timestamp": "2026-10-02T16:48:10",
  "source": "history",
  "shell": "bash",
  "command": "git status",
  "cwd": "/home/user/project",
  "exit_code": 0,
  "duration_ms": 42,
  "host": "my-laptop"
}
```

## Data lifecycle

1. **Capture** — collectors read history / live sessions / transcripts / event logs
2. **Redact** — secrets masked at the boundary
3. **Append** — immutable rows written to SQLite
4. **Detect** — rule-based scans emit alerts
5. **Retain** — optional pruning of data older than a configurable window (Maintenance tab / `retain` API)
6. **Export** — reports generated on demand (HTML/CSV/JSON)

## Retention

Old data is pruned via the **Maintenance** tab or:

```bash
curl -X POST http://127.0.0.1:8765/api/maintenance/retain \
  -H "Content-Type: application/json" -d '{"days": 60}'
```

`GET /api/maintenance/stats` shows DB size, record count, and date span before you prune.

## Backup

To back up the database safely (WAL mode — use the backup API, not a file copy):

```bash
python3 -c "
import sqlite3
src = sqlite3.connect('retrace.db')
dst = sqlite3.connect('retrace-backup.db')
src.backup(dst)
dst.close(); src.close()
"
```