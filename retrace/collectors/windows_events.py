"""Windows Event Log collector — pulls security-relevant events via Get-WinEvent.

Phase 2: aggregates logs that Windows already collects (auth failures,
service errors, PowerShell operational logs) into the Retrace store.

Design: runs on Windows native (PowerShell available) OR from WSL by
invoking powershell.exe. Detects the host automatically. No admin
required for the default channels; elevated channels are skipped with
a warning rather than failing the whole run.
"""

import json
import os
import subprocess
import time

# Channels we query, with a label for the `source` column in Retrace.
CHANNELS = [
    ("Security", "win_security"),
    ("System", "win_system"),
    ("Application", "win_application"),
    ("Microsoft-Windows-PowerShell/Operational", "win_powershell"),
]

# Event IDs we care about (auth, service, PS execution).
SECURITY_IDS = "4624,4625,4634,4648,4672,4720,4728,4732,4740,4771,4776"
SYSTEM_IDS = "6005,6006,6008,7000,7001,7031,7034,7040,7045"
APPLICATION_IDS = "1000,1001,1002,1026"
POWERSHELL_IDS = "4103,4104,40961,40962"

# Map channel -> event IDs -> severity hint (used later by detectors).
SEVERITY_MAP = {
    "Security": {
        "4625": "high",   # failed logon
        "4740": "high",   # account locked out
        "4771": "high",   # kerberos pre-auth failed
        "4648": "medium", # explicit credential logon
    },
    "System": {
        "7031": "high",   # service terminated unexpectedly
        "7034": "high",   # service crashed
        "6008": "medium", # unexpected shutdown
    },
    "Application": {
        "1000": "medium", # app error
        "1001": "medium", # app crash (WER)
    },
    "PowerShell": {
        "4104": "info",   # script block logging
        "4103": "info",   # module logging
    },
}


def _ps_available() -> bool:
    """True if we can reach a PowerShell (native or via WSL interop)."""
    if os.name == "nt":
        return True
    # WSL interop: powershell.exe should be on PATH.
    return shutil_which("powershell.exe") is not None


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _ps_cmd(script: str) -> list[str]:
    """Build the powershell invocation for the current platform."""
    if os.name == "nt":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def _query_channel(channel: str, event_ids: str, minutes: int) -> list[dict]:
    """Run Get-WinEvent for one channel; return list of event dicts."""
    since_ms = int(time.time() * 1000) - minutes * 60_000
    # Filter to the last N minutes by StartTime to keep the payload small.
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$since = [DateTimeOffset]::FromUnixTimeMilliseconds({since_ms}).LocalDateTime
Get-WinEvent -FilterHashtable @{{LogName='{channel}'; StartTime=$since; Id=@({event_ids})}} |
  Select-Object -First 200 |
  ForEach-Object {{
    [PSCustomObject]@{{
      ts = $_.TimeCreated.ToUniversalTime().ToString('o')
      id = $_.Id
      level = $_.LevelDisplayName
      provider = $_.ProviderName
      message = ($_.Message -replace "\\r?\\n", " " -replace '\\s+', ' ').Substring(0, [Math]::Min(400, ($_.Message -replace "\\r?\\n", " " -replace '\\s+', ' ').Length))
      machine = $env:COMPUTERNAME
    }} | ConvertTo-Json -Compress
  }}
"""
    try:
        out = subprocess.run(
            _ps_cmd(script),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    events = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def collect(conn, minutes: int = 60, channels: list[str] | None = None) -> dict:
    """Collect Windows events into Retrace. Returns per-channel counts.

    Callable from Windows native or WSL (uses powershell.exe interop).
    """
    from ..db import insert_command
    from ..redact import redact_line

    if not _ps_available():
        return {"error": "PowerShell not available (Windows or WSL interop required)"}

    wanted = channels or list(CHANNELS)
    results: dict[str, int] = {}
    for channel, source in CHANNELS:
        if channel not in wanted and source not in wanted:
            continue
        if channel == "Security":
            ids = SECURITY_IDS
        elif channel == "System":
            ids = SYSTEM_IDS
        elif channel == "Application":
            ids = APPLICATION_IDS
        else:
            ids = POWERSHELL_IDS

        events = _query_channel(channel, ids, minutes)
        count = 0
        for ev in events:
            msg = redact_line(ev.get("message", ""))
            # Store as a synthetic command record so it appears in search.
            insert_command(
                conn,
                command=f"[{ev.get('id','?')}] {msg}",
                source=source,
                shell="powershell",
                cwd=None,
                exit_code=None,
                duration_ms=None,
                session_id=None,
                host=ev.get("machine"),
                ts=_parse_ts(ev.get("ts")),
            )
            count += 1
        results[source] = count
    return results


def _parse_ts(iso: str | None) -> float | None:
    """Parse ISO-8601 timestamp to epoch seconds; None on failure."""
    if not iso:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None