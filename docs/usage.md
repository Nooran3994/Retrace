# Usage

Retrace ships a single CLI: `retrace` (alias `rec`). Every command is
documented below with examples.

```bash
python3 -m retrace.cli <command>   # from the repo root
retrace <command>                  # if retrace is on PATH
```

## Command Reference

| Command | Description |
|---------|-------------|
| `retrace ingest` | Ingest existing shell history into the DB |
| `retrace ingest --flight` | Also load the flight-recorder spool |
| `retrace ingest --ps` | Also load PowerShell transcripts |
| `retrace hook install` | Install the bash/zsh flight-recorder hook |
| `retrace hook install-ps` | Install the PowerShell transcript hook |
| `retrace win-events` | Collect Windows Event Log entries |
| `retrace remote add/list/remove/test/collect/script` | Remote device collection |
| `retrace search <query>` | Search captured commands |
| `retrace stats` | Show aggregate stats |
| `retrace detect` | Run rule-based detectors over recent records |
| `retrace export --format <fmt>` | Export records (jsonl/csv/json) |
| `retrace web` | Start the local-only web UI (127.0.0.1:8765) |
| `retrace watch` | Periodic ingest + detect daemon |
| `retrace agent` | Persistent agent loop (systemd-friendly) |

## Ingest

```bash
# Ingest shell history (bash/zsh/PowerShell) — the default
retrace ingest

# Ingest history + flight spool + PowerShell transcripts
retrace ingest --flight --ps

# Ingest only the flight spool
retrace ingest --flight
```

Ingest is **idempotent**: re-running does not duplicate rows. History
ingest is per-shell; flight spool dedups by `(ts, cmd)`; transcripts track
processed files in a marker table.

## Flight Recorder Hook

```bash
# Install the bash/zsh hook (appends to ~/.bashrc, idempotent)
retrace hook install
# → "Open a NEW terminal for it to take effect."

# Install the PowerShell transcript hook
retrace hook install-ps
# → "Open a NEW PowerShell window for it to take effect."
```

The bash hook wraps every interactive command via a DEBUG trap and
`PROMPT_COMMAND`, recording: timestamp, command, cwd, exit code, and
duration to `~/.local/share/retrace/spool/flight.jsonl`. Load it with:

```bash
retrace ingest --flight
```

## Windows Event Logs

```bash
# Collect the last 60 minutes from all channels
retrace win-events

# Collect the last 24 hours
retrace win-events --minutes 1440

# Collect only System and Application channels
retrace win-events --channels System Application
```

Channels: `Security`, `System`, `Application`,
`Microsoft-Windows-PowerShell/Operational`. The Security channel requires
elevation; inaccessible channels are skipped with a warning, never fatal.

## Search

```bash
# Basic search
retrace search "kubectl"

# Limit results
retrace search "curl" --limit 20

# Search the last 30 minutes
retrace search "sudo" --since 30
```

Output columns: `id  timestamp  shell  command  [git repo@branch]  (host)`.

## Stats

```bash
retrace stats
# Total records: 5079
# By source:     {'flight': 1775, 'history': 3280, 'win_application': 7, 'win_system': 17}
# Last capture:  2026-09-24 16:06:01
```

## Detection

```bash
# Run detectors over the last 60 minutes (default)
retrace detect

# Look back 24 hours with a larger evaluation window
retrace detect --since 1440 --limit 10000

# Write an example detectors.json config and exit
retrace detect --init-config
```

Output shows severity, rule name, timestamp, message, and occurrence count.
See [Detectors](detectors.md) for the full rule set and extension guide.

## Web UI

```bash
# Start the local-only web UI
retrace web

# Custom port
retrace web --port 9000
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser. The UI
shows live stats, a searchable timeline, and the alert feed. It auto-refreshes
every 15 s. **It binds to 127.0.0.1 only** — it is not reachable from the
network.

## Watch Daemon

```bash
# Run one cycle and exit
retrace watch --once

# Run continuously every 5 minutes
retrace watch --interval 300
```

Each cycle: ingest history + flight spool + transcripts, collect Windows
events, collect registered remotes, run detection, persist alerts.

## Agent

```bash
# Run the persistent agent loop (default 60 s interval)
retrace agent

# One cycle (useful for cron / debugging)
retrace agent --once

# Skip model analysis even if enabled
retrace agent --no-model
```

The agent is the production entry point — it runs under systemd or Task
Scheduler (see [Persistence](persistence.md)) and writes a health state file
to `~/.local/share/retrace/state.json`.

## Export

```bash
# JSONL to stdout
retrace export --format jsonl

# CSV to a file
retrace export --format csv --output ~/retrace-export.csv

# Pretty JSON
retrace export --format json --output out.json
```

Export is **explicit and user-initiated** — nothing leaves the machine unless
you run it. The full `commands` table is exported.

## Remote Collection

```bash
# Register a host
retrace remote add nas 192.168.1.10 --user admin --port 22

# List registered hosts
retrace remote list

# Test SSH connectivity (BatchMode, keys only)
retrace remote test nas

# Collect from one host
retrace remote collect nas

# Collect from every registered host
retrace remote collect --all

# Remove a host
retrace remote remove nas

# Print the agentless collector script (for review)
retrace remote script
```

See [Remote Collection](remote-collection.md) for the full workflow and
security model.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Command error (bad args, host not found, fatal collector error) |
| 130 | Interrupted (Ctrl-C) |