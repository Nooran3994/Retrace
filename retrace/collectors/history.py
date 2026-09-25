"""History collectors — aggregate logs that already exist on the machine.

the logs are already there. We don't need to record
everything from scratch; we ingest what the shells already wrote and
normalize it into the shared schema. Redaction happens at ingest.
"""

import os
import re
import time
from pathlib import Path

from ..redact import redact_line, is_high_entropy

# ---------------------------------------------------------------------------
# Parsers — return (timestamp_epoch_or_None, command_string)
# ---------------------------------------------------------------------------


def parse_bash_history(path: Path) -> list[tuple[float | None, str]]:
    """Parse ~/.bash_history.

    Lines are plain commands; optional timestamps appear as a preceding
    comment `#<epoch>` when HISTTIMEFORMAT is enabled.
    """
    entries: list[tuple[float | None, str]] = []
    pending_ts: float | None = None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and line[1:].strip().isdigit():
            pending_ts = float(line[1:].strip())
            continue
        entries.append((pending_ts, line))
        pending_ts = None
    return entries


def parse_zsh_history(path: Path) -> list[tuple[float | None, str]]:
    """Parse ~/.zsh_history (extended format: `: <ts>:<dur>;<cmd>`)."""
    entries: list[tuple[float | None, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for line in raw.splitlines():
        if not line:
            continue
        m = re.match(r"^:\s*(\d+):\d*;(.*)$", line)
        if m:
            entries.append((float(m.group(1)), m.group(2)))
        else:
            entries.append((None, line))
    return entries


def parse_powershell_history(path: Path) -> list[tuple[float | None, str]]:
    """Parse PowerShell history file.

    Modern PowerShell stores XML (PSReadLine). We extract CommandLine
    elements; timestamps are not stored by PSReadLine, so ts=None.
    """
    entries: list[tuple[float | None, str]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for m in re.finditer(r"<CommandLine>(.*?)</CommandLine>", raw, re.DOTALL):
        cmd = m.group(1).strip()
        if cmd:
            entries.append((None, cmd))
    return entries


# ---------------------------------------------------------------------------
# Plumbing — locate history files and ingest into the DB
# ---------------------------------------------------------------------------

SHELL_META = {
    "bash": ("~/.bash_history", parse_bash_history),
    "zsh": ("~/.zsh_history", parse_zsh_history),
    "powershell": (
        "~/.local/share/powershell/PSReadLine/ConsoleHost_history.txt",
        parse_powershell_history,
    ),
}


def locate_history_files() -> list[tuple[str, Path, callable]]:
    """Find history files that exist on this machine."""
    found: list[tuple[str, Path, callable]] = []
    home = Path.home()
    for shell, (rel, parser) in SHELL_META.items():
        p = Path(os.path.expanduser(rel))
        if not p.is_absolute():
            p = home / rel
        if p.exists():
            found.append((shell, p, parser))
    return found


def ingest_history(conn, host: str | None = None) -> dict:
    """Ingest all found history files into the DB. Returns per-shell counts."""
    host = host or os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown")
    results: dict[str, int] = {}
    for shell, path, parser in locate_history_files():
        count = 0
        for ts, cmd in parser(path):
            if not cmd:
                continue
            redacted = redact_line(cmd)
            if not redacted.strip():
                continue
            from ..db import insert_command

            insert_command(
                conn,
                command=redacted,
                source="history",
                shell=shell,
                host=host,
                ts=ts if ts is not None else None,
            )
            count += 1
        results[shell] = count
    return results