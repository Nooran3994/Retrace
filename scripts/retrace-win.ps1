# retrace-win.ps1 - Windows-native Retrace launcher (Task Scheduler entry point)
# Installs/registers scheduled tasks:
#   - retrace-agent : persistent capture+detect loop (runs every 1 min, restarts on failure)
#   - retrace-web   : local-only web UI on 127.0.0.1:8765 (starts at logon, survives crash)
#
# Task registration works WITHOUT admin: tries Register-ScheduledTask first,
# then falls back to schtasks.exe (per-user task, no elevation required).
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 install
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 uninstall
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 status
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 run-agent   # foreground (for testing)
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\retrace-win.ps1 run-web    # foreground (for testing)

param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'run-agent', 'run-web')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'

# --- Resolve repo root (this script lives in <repo>\scripts\) ---
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe = 'C:\Python314\python.exe'
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Error "Python not found. Install Python 3.10+ or edit `$PythonExe in this script."
    exit 1
}

# --- Paths ---
$LogDir = Join-Path $env:LOCALAPPDATA 'retrace'
$AgentLog = Join-Path $LogDir 'agent.log'
$WebLog   = Join-Path $LogDir 'web.log'
$AgentCmd = Join-Path $RepoRoot 'scripts\retrace-agent.cmd'
$WebCmd   = Join-Path $RepoRoot 'scripts\retrace-web.cmd'
$TaskAgent = 'retrace-agent'
$TaskWeb  = 'retrace-web'

function Write-Status($msg) { Write-Host "[retrace] $msg" -ForegroundColor Cyan }

# --- Register a task without requiring elevation ---
# Tries the modern cmdlet first; if it fails (Access denied / no admin),
# falls back to schtasks.exe which registers a per-user task fine.
function Register-Task($name, $cmdPath, $schedule) {
    $taskExists = $null -ne (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)

    # 1) Try cmdlet path (works when elevated or when user has rights)
    try {
        $action = New-ScheduledTaskAction -Execute $cmdPath -WorkingDirectory $RepoRoot
        if ($schedule -eq 'logon') {
            $trigger = New-ScheduledTaskTrigger -AtLogOn
        } else {
            $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
        }
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -StartWhenAvailable -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Write-Status "Task '$name' registered (trigger: $schedule)"
        return
    } catch {
        # Fall through to schtasks.exe
    }

    # 2) schtasks.exe fallback (no admin needed for current-user tasks)
    $tr = if ($schedule -eq 'logon') { '/SC ONLOGON' } else { '/SC MINUTE /MO 1' }
    $cmd = "`"$cmdPath`""
    & schtasks.exe /Create /TN $name /TR $cmd $tr /F 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Status "Task '$name' registered via schtasks (trigger: $schedule)"
    } else {
        Write-Warning "Task '$name' registration failed. Run the installer from an elevated (Administrator) PowerShell to enable auto-start."
    }
}

function Install-Tasks {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

    # Agent: run every 1 minute (persistent loop, restart-on-failure)
    Register-Task $TaskAgent $AgentCmd 'minutes'
    # Web: start at logon, keep running
    Register-Task $TaskWeb $WebCmd 'logon'

    Write-Status "Installed. Logs: $LogDir"
    Write-Status "Start now: Start-ScheduledTask -TaskName $TaskAgent ; Start-ScheduledTask -TaskName $TaskWeb"
}

function Uninstall-Tasks {
    foreach ($t in @($TaskAgent, $TaskWeb)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Status "Task '$t' removed"
        }
    }
    # Also clear any schtasks-registered variants
    & schtasks.exe /Delete /TN $TaskAgent /F 2>$null | Out-Null
    & schtasks.exe /Delete /TN $TaskWeb /F 2>$null | Out-Null
}

function Show-Status {
    foreach ($t in @($TaskAgent, $TaskWeb)) {
        $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $t
            Write-Host ("{0,-16} {1,-12} LastRun: {2}  LastResult: {3}" -f $t, $task.State, $info.LastRunTime, $info.LastTaskResult) -ForegroundColor Green
        } else {
            Write-Host ("{0,-16} NOT INSTALLED" -f $t) -ForegroundColor Yellow
        }
    }
}

switch ($Action) {
    'install'   { Install-Tasks }
    'uninstall' { Uninstall-Tasks }
    'status'    { Show-Status }
    'run-agent' { & $PythonExe -m retrace.cli agent --interval 60 }
    'run-web'   { & $PythonExe -m retrace.cli web --port 8765 }
}