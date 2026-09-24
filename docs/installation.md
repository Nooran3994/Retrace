# Installation

Retrace is **stdlib-only** — there are no pip dependencies, no virtualenv, no
build step. It needs only Python 3.9+ and git.

## Requirements

| Platform | Requirements |
|----------|--------------|
| Linux | `python3` ≥ 3.9, `git`, optionally `systemd` (user units) |
| macOS | `python3` ≥ 3.9, `git` |
| WSL2 | `python3` ≥ 3.9, `git`; systemd optional (falls back to Task Scheduler) |
| Windows | Python ≥ 3.9 on PATH, `git`, PowerShell |

Check your Python version:

```bash
python3 --version   # must be 3.9 or newer
```

## Quick Install

### Linux / macOS / WSL2

```bash
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
./scripts/install.sh
```

### Windows native (PowerShell)

```powershell
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer is **idempotent** — re-running it is safe; hooks and service
units are never duplicated.

## What the Installer Does

| Step | Details |
|------|---------|
| 1. Platform detection | Native Linux/macOS vs WSL2 vs Windows (bails on git-bash, points to `install.ps1`) |
| 2. Python check | Requires ≥ 3.9; stdlib-only means no pip step |
| 3. DB init | Creates `~/.config/retrace/` and `~/.local/share/retrace/rec.db` |
| 4. Shell hook | Appends the flight-recorder DEBUG-trap to `~/.bashrc` (idempotent) |
| 5. Persistence | systemd user units (agent + web) on Linux/WSL-with-systemd; Task Scheduler otherwise |
| 6. Web UI | Starts `retrace web` on `127.0.0.1:8765` |
| 7. Summary | Prints the URL, data paths, and next commands |

## Installer Options

| Environment Variable | Effect |
|----------------------|--------|
| `RETRACE_PORT=9000` | Custom web UI port (default 8765) |
| `RETRACE_NO_WEB=1` | Skip starting the web UI |
| `RETRACE_NO_PERSIST=1` | Skip systemd / Task Scheduler registration |
| `RETRACE_NO_HOOK=1` | Skip the shell flight-recorder hook |
| `PYTHON=python3.11` | Use a specific Python interpreter |

Example:

```bash
RETRACE_PORT=9000 RETRACE_NO_PERSIST=1 ./scripts/install.sh
```

## Manual Install

If you prefer not to use the installer:

```bash
# 1. Init the DB (creates config + data dirs)
python3 -m retrace.cli stats

# 2. Install the shell flight-recorder hook
python3 -m retrace.cli hook install
#    open a NEW terminal for the hook to take effect

# 3. Start the agent (foreground, Ctrl-C to stop)
python3 -m retrace.cli agent

# 4. In another terminal, start the web UI
python3 -m retrace.cli web
```

### Manual persistence (systemd)

```bash
mkdir -p ~/.config/systemd/user
for u in systemd/retrace-agent.service systemd/retrace-web.service; do
  sed -e "s|/mnt/c/Users/HP/OneDrive/Desktop/SYSTEMS/SCAAI_SYSTEMS/Retrace|$PWD|" \
      -e "s|/home/alfred/.local/share/retrace|$HOME/.local/share/retrace|" \
      "$u" > ~/.config/systemd/user/"$(basename "$u")"
done
systemctl --user daemon-reload
systemctl --user enable --now retrace-agent.service retrace-web.service
```

### Manual persistence (Windows Task Scheduler)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 install
```

## Uninstall

```bash
./scripts/uninstall.sh          # removes hooks, units, tasks
./scripts/uninstall.sh --keep-data   # keep the SQLite database
```

Windows: `powershell -File scripts\retrace-win.ps1 uninstall`

## Post-Install Verification

```bash
retrace stats                    # shows total records, sources, last capture
retrace detect                   # runs rule-based detection (expect "No alerts")
retrace web                      # opens http://127.0.0.1:8765
```

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `python3 not found` | Install Python 3.9+ and re-run |
| `Python 3.8 too old` | Retrace needs ≥ 3.9 (uses `from __future__ import annotations`) |
| Hook not capturing | The hook only activates in **new** terminals; re-open your shell |
| Web UI unreachable | Confirm it binds to `127.0.0.1:8765`; check `systemctl --user status retrace-web` |
| Windows events empty | The Security channel needs elevation; other channels work unelevated |