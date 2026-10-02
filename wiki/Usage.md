# Usage

Retrace is controlled from the terminal and, since v0.2.0, from the web UI.

## Core commands

```bash
retrace stats              # Summary of records, hosts, alerts
retrace ingest             # Pull existing shell history into the DB
retrace ingest --flight    # Ingest live flight-recorder session data
retrace detect             # Run rule-based detection over recorded data
retrace web                # Start the local web UI (default port 8765)
retrace web --port 55555   # Start on a specific port
retrace hook install       # Install the shell hook (captures live sessions)
retrace hook uninstall     # Remove the shell hook
retrace agent --interval 60  # Run the background collector every 60s
retrace win-events         # Collect Windows Event Logs (Windows only)
retrace collect-remote     # Collect from registered remote hosts
retrace search "pattern"   # Search recorded commands
```

## Web UI

Start the UI and open [http://127.0.0.1:8765](http://127.0.0.1:8765):

```bash
retrace web
```

The UI has seven tabs:

| Tab | What it does |
|---|---|
| Dashboard | Stat cards + SVG charts (heatmap, hourly/weekly activity, top commands) |
| Timeline | Searchable command history |
| Alerts | Detected activity, acknowledge |
| Remotes | Registered SSH hosts + record counts |
| Maintenance | DB stats, retention (prune old data), vacuum |
| Reports | Download HTML/CSV/JSON reports |
| Settings | Config, detectors, remotes, one-click CLI actions |

## Configuration

Config lives in `config.json` (created on first run, typically under
`~/.config/retrace/` or the project dir). Edit it via the **Settings** tab in
the UI, or directly:

```json
{
  "model": {
    "provider": "ollama",
    "url": "http://127.0.0.1:11434",
    "model": "llama3.2",
    "enabled": false
  },
  "watch_interval": 60
}
```

## Optional local model analysis

Model analysis is **opt-in and advisory only** — the core capture/detect loop
never depends on a model. To enable:

1. Run a local model server (e.g., Ollama at `http://127.0.0.1:11434`).
2. Set `model.enabled: true` in config (via Settings tab or config.json).
3. No API calls, no data collected — inference stays on your machine.

## Data retention

Old data can be pruned from the **Maintenance** tab (or via API):

```bash
curl -X POST http://127.0.0.1:8765/api/maintenance/retain \
  -H "Content-Type: application/json" -d '{"days": 60}'
```

This keeps the database lean while preserving recent history.