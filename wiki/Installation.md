# Installation

Retrace is stdlib-only Python 3.9+, with one-click installers for Linux/WSL2 and Windows.

## Quick install (Linux / macOS / WSL2)

```bash
curl -fsSL https://raw.githubusercontent.com/Nooran3994/Retrace/main/scripts/install.sh | bash
```

## Quick install (Windows)

```powershell
# PowerShell (run as normal user; installer handles permissions)
.\scripts\install.ps1
```

Or download the installer from the repo and run it from the folder.

## Manual install

```bash
git clone https://github.com/Nooran3994/Retrace.git
cd Retrace
python3 -m retrace.cli stats      # creates DB + config dirs
python3 -m retrace.cli hook install
python3 -m retrace.cli ingest
python3 -m retrace.cli web        # local UI at http://127.0.0.1:8765
```

No virtualenv required. `pip install -e .` is optional if you want the `retrace` entry point.

## Verify it works

```bash
retrace stats
```

You should see a summary of records, hosts, and alert counts.

## Uninstall

```bash
# Linux / WSL2
bash scripts/uninstall.sh

# Windows
.\scripts\uninstall.ps1
```

## Requirements

- Python 3.9+ (no pip dependencies — stdlib only)
- Linux/WSL2: `bash`, `python3`
- Windows: PowerShell 5.1+, Python 3.9+

## Troubleshooting

| Problem | Fix |
|---|---|
| `retrace: command not found` | Ensure the install dir is on `PATH`, or use `python3 -m retrace.cli` |
| Web UI won't start (`WinError 10013`) | Windows security software blocks low ports — use `retrace web --port 55555` |
| No data appears | Run `retrace ingest` once to pull existing shell history |