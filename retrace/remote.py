"""Remote device collector — agentless, over SSH.

the logs are already on every machine. Retrace doesn't
need an agent installed on remote devices; it pushes a small read-only
script over SSH that reads existing history files + flight spool and
emits normalized JSONL. The local side redacts and ingests into the
shared SQLite DB, tagged with the remote hostname.

Security model:
  - No agent install on remotes (nothing persistent, nothing to secure)
  - Transport: SSH only (encrypted). BatchMode=yes — no password prompts,
    keys only. No credentials ever stored by Retrace.
  - Redaction happens locally at ingest (same engine as everything else).
    In transit the data is inside the SSH tunnel, so secrets are not
    exposed to the network.
  - Hosts registry is a local JSON file with 0600 perms.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .redact import redact_line, is_high_entropy

# ---------------------------------------------------------------------------
# Host registry (~/.config/retrace/hosts.json)
# ---------------------------------------------------------------------------


def hosts_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "retrace" / "hosts.json"


def _ensure_hosts_file() -> Path:
    p = hosts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("[]", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


def load_hosts() -> list[dict]:
    p = _ensure_hosts_file()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_hosts(hosts: list[dict]) -> None:
    p = _ensure_hosts_file()
    p.write_text(json.dumps(hosts, indent=2), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def add_host(name: str, host: str, user: str | None = None, port: int = 22) -> dict:
    """Register a remote host. Returns the host record (raises on dup)."""
    hosts = load_hosts()
    if any(h.get("name") == name for h in hosts):
        raise ValueError(f"Host '{name}' already registered")
    rec = {"name": name, "host": host, "user": user, "port": port}
    hosts.append(rec)
    save_hosts(hosts)
    return rec


def remove_host(name: str) -> bool:
    hosts = load_hosts()
    kept = [h for h in hosts if h.get("name") != name]
    if len(kept) == len(hosts):
        return False
    save_hosts(kept)
    return True


def get_host(name: str) -> dict:
    for h in load_hosts():
        if h.get("name") == name:
            return h
    raise KeyError(f"Host '{name}' not found")


# ---------------------------------------------------------------------------
# Remote collector script — pushed over SSH, runs ON the remote, emits JSONL
# ---------------------------------------------------------------------------

REMOTE_SCRIPT = r"""#!/usr/bin/env bash
# Retrace remote collector (agentless). Runs on the remote via SSH.
# Reads existing history files + flight spool, emits JSONL to stdout.
# Nothing is written, nothing is stored. Read-only.
set -u
HOST="$(hostname 2>/dev/null || echo unknown)"

# JSON-encode a string using python3 if available, else minimal escaping.
json_enc() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
  else
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
  fi
}

emit() {
  # $1 = ts (or empty), $2 = shell, $3 = command
  local ts="$1" shell="$2" cmd="$3"
  [ -z "$cmd" ] && return
  local enc
  enc="$(printf '%s' "$cmd" | json_enc)"
  if [ -n "$ts" ]; then
    printf '{"ts":%s,"shell":"%s","host":"%s","cmd":%s}\n' "$ts" "$shell" "$HOST" "$enc"
  else
    printf '{"ts":null,"shell":"%s","host":"%s","cmd":%s}\n' "$shell" "$HOST" "$enc"
  fi
}

# --- bash history (with optional #<epoch> timestamps) ---
if [ -f "${HOME}/.bash_history" ]; then
  pending=""
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      '' ) continue ;;
      '#'*) if echo "$line" | grep -qE '^#[0-9]+$'; then
              pending="${line#\#}"
            fi
            continue ;;
    esac
    emit "$pending" "bash" "$line"
    pending=""
  done < "${HOME}/.bash_history"
fi

# --- zsh history (extended format: `: <ts>:<dur>;<cmd>`) ---
if [ -f "${HOME}/.zsh_history" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [ -z "$line" ] && continue
    if echo "$line" | grep -qE '^:\s*[0-9]+:[0-9]*;'; then
      ts="$(echo "$line" | sed -E 's/^:\s*([0-9]+):.*/\1/')"
      cmd="$(echo "$line" | sed -E 's/^:\s*[0-9]+:[0-9]*;//')"
      emit "$ts" "zsh" "$cmd"
    else
      emit "" "zsh" "$line"
    fi
  done < "${HOME}/.zsh_history"
fi

# --- PowerShell history (PSReadLine XML) ---
if [ -f "${HOME}/.local/share/powershell/PSReadLine/ConsoleHost_history.txt" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [ -z "$line" ] && continue
    emit "" "powershell" "$line"
  done < "${HOME}/.local/share/powershell/PSReadLine/ConsoleHost_history.txt"
fi

# --- flight spool (if Retrace hook is installed on the remote) ---
SPOOL="${XDG_DATA_HOME:-${HOME}/.local/share}/retrace/spool/flight.jsonl"
if [ -f "$SPOOL" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] && continue
    # Already JSON — just ensure host is set
    echo "$line" | sed "s/\"host\":\"[^\"]*\"/\"host\":\"$HOST\"/"
  done < "$SPOOL"
fi
"""


def _ssh_cmd(rec: dict, extra: list[str] | None = None) -> list[str]:
    user = rec.get("user") or None
    host = rec.get("host", "")
    port = int(rec.get("port") or 22)
    target = f"{user}@{host}" if user else host
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new",
        "-p", str(port),
        target,
    ]
    if extra:
        cmd += extra
    return cmd


def test_connection(rec: dict) -> tuple[bool, str]:
    """Verify SSH connectivity without collecting anything."""
    try:
        proc = subprocess.run(
            _ssh_cmd(rec, ["true"]),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return True, "ok"
        return False, proc.stderr.strip() or f"exit {proc.returncode}"
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def collect_host(rec: dict, db_conn=None) -> dict:
    """SSH into the remote, run the collector script, redact + ingest.

    Returns {loaded, skipped, error?}.
    """
    from .db import connect, insert_command

    own_conn = db_conn is None
    conn = db_conn or connect()
    try:
        # Feed the script via stdin so nothing is written on the remote.
        proc = subprocess.run(
            _ssh_cmd(rec, ["bash", "-s"]),
            input=REMOTE_SCRIPT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return {"loaded": 0, "skipped": 0,
                    "error": proc.stderr.strip() or f"ssh exit {proc.returncode}"}

        loaded = skipped = 0
        seen: set[tuple] = set()
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                rec_data = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            cmd = redact_line(rec_data.get("cmd", ""))
            if not cmd.strip():
                skipped += 1
                continue
            host = rec_data.get("host") or rec.get("host")
            ts = rec_data.get("ts")
            key = (host, ts, cmd)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)

            # Dedup against existing DB rows for this host.
            dup = conn.execute(
                "SELECT 1 FROM commands WHERE host=? AND ts=? AND command=? LIMIT 1",
                (host, ts if ts is not None else 0, cmd),
            ).fetchone()
            if dup:
                skipped += 1
                continue

            insert_command(
                conn,
                command=cmd,
                source="remote",
                shell=rec_data.get("shell", "unknown"),
                host=host,
                entropy=1 if is_high_entropy(rec_data.get("cmd", "")) else 0,
                ts=ts if ts is not None else None,
            )
            loaded += 1
        return {"loaded": loaded, "skipped": skipped}
    finally:
        if own_conn:
            conn.close()


def collect_all(db_conn=None) -> dict:
    """Collect from every registered host. Returns per-host results."""
    results: dict[str, dict] = {}
    for rec in load_hosts():
        name = rec.get("name", rec.get("host"))
        ok, msg = test_connection(rec)
        if not ok:
            results[name] = {"loaded": 0, "skipped": 0, "error": f"unreachable: {msg}"}
            continue
        results[name] = collect_host(rec, db_conn=db_conn)
    return results


def script_only() -> str:
    """Print the remote collector script (for review or manual use)."""
    return REMOTE_SCRIPT