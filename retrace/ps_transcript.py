"""PowerShell transcript hook — live capture on Windows native.

Phase 2: mirrors the bash DEBUG-trap flight recorder for PowerShell.
Uses Start-Transcript to capture every command + output into a session
log, then `retrace ingest --ps` parses & redacts it into the DB.

Design: transcripts are per-session files under
%LOCALAPPDATA%\\retrace\\transcripts\\*.txt. The ingest step is
idempotent (tracks processed files in a marker table).
"""

import os
import re
import time
from pathlib import Path

TRANSCRIPT_DIR_NAME = "retrace\\transcripts"

PROFILE_HOOK = r"""
# --- Retrace flight recorder hook (PowerShell) ---
$retraceTransDir = Join-Path $env:LOCALAPPDATA "retrace\transcripts"
New-Item -ItemType Directory -Force -Path $retraceTransDir | Out-Null
$retraceStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$retraceSess = Join-Path $retraceTransDir "session-$retraceStamp-$PID.txt"
Start-Transcript -Path $retraceSess -Append
"""

# PowerShell transcript line patterns:
#   PS C:\path> command
#   PS> command
PROMPT_RE = re.compile(r"^(PS [^>]*>)\s*(.*)$")
# Windows PowerShell header line
WINDOW_HEADER_RE = re.compile(r"^Windows PowerShell.*$|^Transcript started.*$|^Transcript stopped.*$")


def transcript_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "retrace" / "transcripts"


def install_ps_hook(profile_path: Path | None = None) -> Path:
    """Append the transcript hook to the PowerShell profile (idempotent)."""
    profile = profile_path or Path.home() / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    if os.name != "nt":
        # WSL: profile lives under /mnt/c/Users/<user>/Documents/...
        win_home = Path("/mnt/c/Users/HP")
        if win_home.exists():
            profile = win_home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    profile.parent.mkdir(parents=True, exist_ok=True)
    marker = "# --- Retrace flight recorder hook (PowerShell) ---"
    if profile.exists() and marker in profile.read_text(encoding="utf-8", errors="replace"):
        return profile
    with profile.open("a", encoding="utf-8") as f:
        f.write("\n" + PROFILE_HOOK + "\n")
    return profile


def _parse_transcript(text: str) -> list[dict]:
    """Parse a transcript file into command records."""
    records = []
    current_cwd = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if WINDOW_HEADER_RE.match(line):
            continue
        m = PROMPT_RE.match(line)
        if m:
            prompt, cmd = m.group(1), m.group(2).strip()
            if not cmd:
                continue
            # Extract cwd from prompt like PS C:\Users\HP>
            cwd_m = re.search(r"PS (.+)>", prompt)
            if cwd_m:
                current_cwd = cwd_m.group(1).strip()
            records.append(
                {
                    "ts": time.time(),
                    "shell": "powershell",
                    "cmd": cmd,
                    "cwd": current_cwd,
                }
            )
    return records


def ingest_transcripts(conn) -> dict:
    """Parse & load all unprocessed transcript files (idempotent)."""
    from .db import insert_command
    from .redact import redact_line, is_high_entropy

    conn.execute(
        """CREATE TABLE IF NOT EXISTS ps_ingested (
               path TEXT PRIMARY KEY,
               ts REAL NOT NULL
           )"""
    )
    conn.commit()

    d = transcript_dir()
    if not d.exists():
        return {"loaded": 0, "skipped": 0, "files": 0}

    loaded = skipped = files = 0
    for p in sorted(d.glob("session-*.txt")):
        # Skip already-processed files
        row = conn.execute("SELECT 1 FROM ps_ingested WHERE path=?", (str(p),)).fetchone()
        if row:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        records = _parse_transcript(text)
        for rec in records:
            cmd = redact_line(rec["cmd"])
            if not cmd.strip():
                skipped += 1
                continue
            insert_command(
                conn,
                command=cmd,
                source="ps_transcript",
                shell="powershell",
                cwd=rec.get("cwd"),
                entropy=1 if is_high_entropy(rec["cmd"]) else 0,
                ts=rec.get("ts"),
            )
            loaded += 1
        conn.execute("INSERT OR REPLACE INTO ps_ingested VALUES (?,?)", (str(p), time.time()))
        conn.commit()
        files += 1
    return {"loaded": loaded, "skipped": skipped, "files": files}