# install.ps1 — Retrace one-command installer for Windows (PowerShell 5.1+)
#
# Does (idempotently):
#   1. Verify python >= 3.9 (stdlib-only — no pip installs)
#   2. Init config + data dirs + SQLite DB
#   3. Install the PowerShell transcript hook
#   4. Install the `retrace.cmd` launcher on the user PATH
#   5. Register Task Scheduler persistence (agent + web)
#   6. Start the local-only web UI (127.0.0.1:8765)
#   7. Print summary + next commands
#
# Usage (run from repo root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install.ps1
#   $env:RETRACE_PORT = 9000; powershell ... scripts\install.ps1
#   $env:RETRACE_NO_WEB = 1;  powershell ... scripts\install.ps1
#
$ErrorActionPreference = 'Stop'

function Write-Info($msg)  { Write-Host "[retrace] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[retrace] OK: $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[retrace] WARN: $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[retrace] FAIL: $msg" -ForegroundColor Red; exit 1 }

# ── 0. Repo root ──
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$Port = if ($env:RETRACE_PORT) { $env:RETRACE_PORT } else { 8765 }

# ── 1. Find python ──
$PythonExe = 'C:\Python314\python.exe'
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Fail "Python not found. Install Python 3.9+ and re-run."
}
$PyVer = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$VerParts = $PyVer.Split('.')
if ([int]$VerParts[0] -lt 3 -or ([int]$VerParts[0] -eq 3 -and [int]$VerParts[1] -lt 9)) {
    Write-Fail "Python $PyVer too old — need 3.9+."
}
Write-Ok "python $PyVer (stdlib-only)"

# ── 2. Init DB (stats auto-creates) ──
Write-Info "Initialising database…"
& $PythonExe -m retrace.cli stats | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail "DB init failed." }
Write-Ok "database ready"

# ── 3. Install PowerShell transcript hook ──
if ($env:RETRACE_NO_HOOK -ne '1') {
    Write-Info "Installing PowerShell transcript hook…"
    & $PythonExe -m retrace.cli hook install-ps | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warn "hook install failed (continuing)" }
    else { Write-Ok "transcript hook installed (open a NEW PowerShell window to activate)" }
}

# ── 4. Install `retrace.cmd` launcher on the USER PATH ──
#    This is what makes `retrace` work from ANY terminal and ANY
#    directory — without pip, venv, or admin rights.
Write-Info "Installing 'retrace.cmd' launcher on user PATH…"
$ScriptsDir = Join-Path $RepoRoot 'scripts'
$UserPath = [Environment]::GetValue('Path', 'USER')
if ($null -eq $UserPath) { $UserPath = '' }
if ($UserPath -notlike "*$ScriptsDir*") {
    # [Environment]::SetValue writes to the registry (permanent, no setx truncation)
    [Environment]::SetValue('Path', "$UserPath;$ScriptsDir", 'USER')
    Write-Ok "launcher added to user PATH — open a NEW terminal to use 'retrace'"
} else {
    Write-Ok "launcher already on user PATH"
}

# ── 5. Register Task Scheduler persistence ──
if ($env:RETRACE_NO_PERSIST -ne '1') {
    Write-Info "Registering Task Scheduler tasks…"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'scripts\retrace-win.ps1') install
    if ($LASTEXITCODE -ne 0) { Write-Warn "Task registration had issues (may need elevated shell for web task)" }
    else { Write-Ok "persistence registered (agent every 1min, web at logon)" }
}

# ── 6. Start web UI ──
if ($env:RETRACE_NO_WEB -ne '1') {
    Write-Info "Starting web UI on 127.0.0.1:$Port …"
    Start-Process -WindowStyle Hidden -FilePath $PythonExe -ArgumentList "-m","retrace.cli","web","--port","$Port"
    Start-Sleep -Seconds 1
    Write-Ok "web UI started — http://127.0.0.1:$Port"
}

# ── 7. Summary ──
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════════╗"
Write-Host "║                     Retrace installed ✓                        ║"
Write-Host "╠═══════════════════════════════════════════════════════════════╣"
Write-Host "║  Web UI :  http://127.0.0.1:$Port (localhost only)             ║"
Write-Host "║  Data   :  %LOCALAPPDATA%\retrace\rec.db                       ║"
Write-Host "║  Config :  %APPDATA%\retrace\                                  ║"
Write-Host "║  Launcher: retrace (on user PATH — any terminal, any directory)║"
Write-Host "╠═══════════════════════════════════════════════════════════════╣"
Write-Host "║  Try:                                                          ║"
Write-Host "║    retrace stats        — see what's captured                  ║"
Write-Host "║    retrace search curl  — search your commands                 ║"
Write-Host "║    retrace detect       — rule-based security alerts           ║"
Write-Host "║    retrace export --format jsonl --output out.jsonl            ║"
Write-Host "║    retrace web          — re-open the UI                       ║"
Write-Host "╚═══════════════════════════════════════════════════════════════╝"
Write-Host ""
Write-Ok "Done. Run 'retrace stats' to confirm."