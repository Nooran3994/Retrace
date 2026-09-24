# Persistence

Retrace is designed to run 24/7. Two persistence layers are supported:
**systemd user units** (Linux / WSL2 with systemd) and **Windows Task
Scheduler** (Windows native / WSL2 without systemd). The installer picks the
right one automatically.

## What Runs

| Unit / Task | Command | Schedule |
|-------------|---------|----------|
| `retrace-agent.service` | `python3 -m retrace.cli agent` | every 60 s (loop) |
| `retrace-web.service` | `python3 -m retrace.cli web --port 8765` | always listening |

The agent loop: ingest history + flight spool + transcripts → collect Windows
events → collect remotes → run detection → persist alerts → write state. It
restarts on crash (`Restart=on-failure`) and skips a cycle if it overruns the
interval.

The web service serves the local-only UI. It also restarts on failure.

## systemd (Linux / WSL2 with systemd)

The unit files live in `systemd/` in the repo. The installer copies them to
`~/.config/systemd/user/` with paths rewritten for the current machine.

```bash
# Install (via installer or manually)
systemctl --user daemon-reload
systemctl --user enable --now retrace-agent.service retrace-web.service

# Status
systemctl --user status retrace-agent.service
systemctl --user status retrace-web.service

# Logs
journalctl --user -u retrace-agent.service -f
journalctl --user -u retrace-web.service -f

# Restart
systemctl --user restart retrace-agent.service

# Stop / disable
systemctl --user stop retrace-agent.service
systemctl --user disable retrace-agent.service
```

### Unit file details

`systemd/retrace-agent.service`:

```ini
[Unit]
Description=Retrace agent (capture + detect loop)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m retrace.cli agent --no-model
Restart=on-failure
RestartSec=5
```

`systemd/retrace-web.service`:

```ini
[Unit]
Description=Retrace local web UI
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m retrace.cli web --port 8765
Restart=on-failure
RestartSec=10
```

## Windows Task Scheduler

Windows native (or WSL2 without systemd) uses `scripts/retrace-win.ps1`:

```powershell
# Install the agent + web tasks
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 install

# Uninstall
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 uninstall
```

The script registers two tasks:

| Task | Command | Trigger |
|------|---------|---------|
| `retrace-agent` | `python retrace\cli.py agent` | every 1 minute |
| `retrace-web` | `python retrace\cli.py web` | at logon |

Verify with:

```powershell
schtasks /Query /TN "retrace-agent"
schtasks /Run /TN "retrace-agent"
```

## Health & State

The agent writes `~/.local/share/retrace/state.json` after every cycle:

```json
{
  "last_cycle": 1788100000.0,
  "last_cycle_end": 1788100060.0,
  "interval_s": 60,
  "summary": {
    "ingested": 56,
    "events": 17,
    "remote": 0,
    "alerts": 0,
    "insights": 0,
    "errors": []
  },
  "pid": 1357
}
```

The web UI's `/api/stats` endpoint reads the DB directly for totals, sources,
and last capture time.

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| Agent not running | `systemctl --user status retrace-agent`; check `journalctl --user -u retrace-agent -n 50` |
| Web UI not reachable | `systemctl --user status retrace-web`; confirm port 8765 free: `ss -tlnp \| grep 8765` |
| Task Scheduler task missing | Re-run `scripts/retrace-win.ps1 install` from PowerShell |
| Cycle errors in state.json | Read `summary.errors` — each collector is isolated; one failure never stops the loop |
| WSL systemd unavailable | Use the Task Scheduler fallback: `powershell.exe -File scripts/retrace-win.ps1 install` |