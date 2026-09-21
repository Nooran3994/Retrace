# retrace-run-agent.ps1 — Windows-native agent loop wrapper (called by Task Scheduler)
# Runs the persistent capture+detect cycle every 60s using the Windows Python.
# The DB is platform-aware: Windows native uses %LOCALAPPDATA%\retrace\rec.db
# (separate from the WSL DB — both are local, both redact before storage).

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe = 'C:\Python314\python.exe'
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) { Write-Error "Python not found"; exit 1 }

Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

# --interval 60 = one full cycle per minute; systemd/Task Scheduler restarts if it dies
& $PythonExe -m retrace.cli agent --interval 60 --no-model