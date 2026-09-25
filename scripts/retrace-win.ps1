# retrace-win.ps1 - Windows-native Retrace launcher (Task Scheduler entry point)
# Installs/registers scheduled tasks:
#   - retrace-agent : persistent capture+detect loop (runs every 1 min, restarts on failure)
#   - retrace-web   : local-only web UI on 127.0.0.1:8765 (starts at logon, survives crash)
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
$AgentPs1 = Join-Path $RepoRoot 'scripts\retrace-run-agent.ps1'
$WebPs1   = Join-Path $RepoRoot 'scripts\retrace-run-web.ps1'
$TaskAgent = 'retrace-agent'
$TaskWeb  = 'retrace-web'

function Write-Status($msg) { Write-Host "[retrace] $msg" -ForegroundColor Cyan }

function New-Task($name, $ps1, $log, $triggerType) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`" *>> `"$log`"" `
        -WorkingDirectory $RepoRoot
    $trigger = switch ($triggerType) {
        'logon'   { New-ScheduledTaskTrigger -AtLogOn }
        'minutes' { New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1) }
    }
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Status "Task '$name' registered (trigger: $triggerType)"
}

function Install-Tasks {
    # Ensure log dir
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

    # Agent: run every 1 minute (persistent loop, restart-on-failure)
    New-Task $TaskAgent $AgentPs1 $AgentLog 'minutes'
    # Web: start at logon, keep running
    New-Task $TaskWeb $WebPs1 $WebLog 'logon'

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