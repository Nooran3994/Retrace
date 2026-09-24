# Retrace Documentation

Retrace is a **deterministic, offline, agentic-free terminal flight recorder and
security observability tool**. It aggregates logs that already exist on your
machine — shell history, live terminal sessions, PowerShell transcripts, Windows
Event Logs, and (optionally) the history of remote devices over SSH — redacts
secrets before anything is stored, and runs rule-based detection locally with no
network dependency and no API calls.

This documentation set covers the complete system: architecture, installation,
usage, security model, detection rules, remote collection, persistence, and the
data model.

## Documentation Index

| Document | Purpose |
|----------|---------|
| [Architecture](architecture.md) | System design, modules, data flow, design principles |
| [Installation](installation.md) | Install on Linux, macOS, WSL2, and Windows native |
| [Usage](usage.md) | Complete CLI reference with examples |
| [Security](security.md) | Threat model, redaction engine, integrity guarantees |
| [Detectors](detectors.md) | Built-in rules, severities, and how to extend them |
| [Remote Collection](remote-collection.md) | Agentless SSH collection from other devices |
| [Persistence](persistence.md) | Running 24/7 via systemd and Windows Task Scheduler |
| [Data Model](data-model.md) | SQLite schema, sources, and query patterns |
| [Development](development.md) | Codebase walkthrough, testing, contributing |

## Quick Start

```bash
# Clone and install (Linux / macOS / WSL2)
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
./scripts/install.sh

# Windows native (PowerShell)
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

See [Installation](installation.md) for full details, including manual installs
and environment variables.

## Core Principles

1. **The logs are already there.** Retrace aggregates what shells and Windows
   already record — it does not require an agent on every machine.
2. **Redaction first.** Secrets are masked *before* anything is stored. The
   original text is discarded.
3. **Offline by default.** No network calls, no telemetry, no API keys. The web
   UI binds strictly to `127.0.0.1`.
4. **Deterministic over agentic.** Detection is rule-based and offline. Local
   model analysis is optional, opt-in, and advisory only.
5. **Append-only storage.** Rows are immutable once written, keeping replay
   timelines honest.

## Quick Command Tour

```bash
retrace stats                          # what's captured
retrace search "curl"                  # search commands
retrace detect                         # run rule-based detection
retrace export --format jsonl -o out.jsonl
retrace web                            # local-only web UI
retrace remote add nas 192.168.1.10    # collect from another device
```

Every command is documented in [Usage](usage.md).