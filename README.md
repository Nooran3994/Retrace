# Retrace

Deterministic, offline terminal intelligence. Captures shell history, live flight-recorder data, PowerShell transcripts, and Windows Event Logs — then runs rule-based detection. No LLM required for core value. No data leaves the machine unless you export it.

## Commands

| Command | What it does |
|---------|--------------|
| `retrace ingest` | Ingest shell history into the local DB |
| `retrace ingest --flight` | Load flight-recorder spool |
| `retrace ingest --ps` | Load PowerShell transcripts |
| `retrace hook install` | Install bash flight-recorder hook |
| `retrace hook install-ps` | Install PowerShell transcript hook |
| `retrace win-events` | Collect Windows Event Log entries |
| `retrace search <q>` | Search captured commands |
| `retrace stats` | Show aggregate stats |
| `retrace detect` | Run rule-based detectors |
| `retrace web` | Local-only web UI (127.0.0.1:8765) |
| `retrace watch` | Daemon: periodic ingest + detect |
| `retrace export --format jsonl` | Export records (jsonl/csv/json) |

## Security model

- **Local-only by default**: web UI binds to `127.0.0.1`, never `0.0.0.0`. No external assets, no CDN, no telemetry.
- **Redaction-first**: secrets (PATs, JWTs, Bearer, `KEY=value`) are masked before storage; high-entropy strings are flagged, not stored.
- **Append-only SQLite**: rows immutable once written; file perms locked to current user; WAL mode.
- **Deterministic detection**: 10 built-in rules (pipe-to-shell, curl|sh supply-chain, secret-on-CLI, auth-failure spikes, destructive commands, exfiltration patterns, long-running commands). User-extensible via `~/.config/retrace/detectors.json`.
- **Export is explicit**: data leaves only when you run `retrace export`.

## Install

```bash
pip install -e .
retrace ingest
retrace hook install   # open a new terminal
retrace web            # http://127.0.0.1:8765
```

## Roadmap

- [x] Phase 1 — history ingest, redaction, query, export
- [x] Phase 2 — Windows Event Logs, PowerShell transcripts, detectors
- [x] Phase 3 — local web UI, watch daemon, persisted alerts
- [ ] Remote device agent (opt-in SSH collector)
- [ ] Optional local-LLM analysis (Ollama, no API calls)
