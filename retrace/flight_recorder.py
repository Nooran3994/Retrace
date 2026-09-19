"""Flight Recorder — live capture of terminal sessions.

The core of Retrace. When active, it wraps shell execution (via a
DEBUG trap in bash/zsh, or a PowerShell transcript) and records every
command with its exit code, duration, cwd, git context, and redacted
output — like a flight recorder on an aircraft: always on, tamper-
evident, replayable.

Phase 1 implementation: a bash/zsh DEBUG-trap hook script that logs
to a local JSONL spool; the `retrace ingest --flight` command loads
the spool into SQLite. (A native PTY wrapper arrives in Phase 2.)
"""

import json
import os
import time
from pathlib import Path

HOOK_BASH = r"""
# --- Retrace flight recorder hook (bash) ---
_retrace_spool="${XDG_DATA_HOME:-$HOME/.local/share}/retrace/spool"
mkdir -p "$_retrace_spool"
_retrace_pre() {
  _retrace_cmd="$BASH_COMMAND"
  _retrace_start=$(date +%s%N)
  _retrace_pwd="$PWD"
}
_retrace_post() {
  local code=$?
  local end=$(date +%s%N)
  local dur=$(( (end - _retrace_start) / 1000000 ))
  local line
  line=$(printf '{"ts":%s,"shell":"bash","cmd":%s,"cwd":%s,"exit":%d,"dur_ms":%d}' \
    "$(date +%s)" "$(printf '%s' "$_retrace_cmd" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
    "$(printf '%s' "$_retrace_pwd" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
    "$code" "$dur")
  printf '%s\n' "$line" >> "$_retrace_spool/flight.jsonl"
}
trap '_retrace_pre' DEBUG
trap '_retrace_post' DEBUG
"""


def spool_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "retrace" / "spool" / "flight.jsonl"


def install_bash_hook(rc_path: Path | None = None) -> Path:
    """Append the flight recorder hook to ~/.bashrc (idempotent)."""
    rc = rc_path or Path.home() / ".bashrc"
    marker = "# --- Retrace flight recorder hook (bash) ---"
    if rc.exists() and marker in rc.read_text(encoding="utf-8", errors="replace"):
        return rc
    with rc.open("a", encoding="utf-8") as f:
        f.write("\n" + HOOK_BASH + "\n")
    return rc


def ingest_spool(conn) -> dict:
    """Load spooled flight records into SQLite (idempotent by ts+cmd)."""
    from .redact import redact_line, is_high_entropy

    spool = spool_path()
    if not spool.exists():
        return {"loaded": 0, "skipped": 0}

    loaded = skipped = 0
    seen = set()
    for line in spool.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        key = (rec.get("ts"), rec.get("cmd"))
        if key in seen:
            continue
        seen.add(key)
        cmd = redact_line(rec.get("cmd", ""))
        if not cmd.strip():
            skipped += 1
            continue
        from .db import insert_command

        insert_command(
            conn,
            command=cmd,
            source="flight",
            shell=rec.get("shell", "bash"),
            cwd=rec.get("cwd"),
            exit_code=rec.get("exit"),
            duration_ms=rec.get("dur_ms"),
            entropy=1 if is_high_entropy(rec.get("cmd", "")) else 0,
            ts=rec.get("ts"),
        )
        loaded += 1
    return {"loaded": loaded, "skipped": skipped}