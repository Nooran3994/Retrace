# retrace-run-web.ps1 — Windows-native web UI launcher (called by Task Scheduler at logon)
# Serves the local-only timeline UI on 127.0.0.1:8765. Binds to loopback only.

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe = 'C:\Python314\python.exe'
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) { Write-Error "Python not found"; exit 1 }

Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

& $PythonExe -m retrace.cli web --port 8765