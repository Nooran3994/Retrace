"""Detectors engine — deterministic, offline rule-based alerting.

The 'intelligent code, not agentic' layer of Retrace. Every rule is a
pure function over the captured command stream. No LLM, no network,
no external calls. Rules are declarative and user-extensible via a
JSON config file at ~/.config/retrace/detectors.json.

Each rule:
  - name        unique id (kebab-case)
  - severity    info | medium | high | critical
  - description what it looks for
  - match       regex applied to the command text (case-insensitive)
  - window_s    optional: time window in seconds for counting rules
  - threshold   optional: min occurrences within window to fire
  - message     human-readable alert body (may use {count})
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Built-in rule set — curated for the common terminal threats
# ---------------------------------------------------------------------------

BUILTIN_RULES: list[dict[str, Any]] = [
    {
        "name": "pipe-to-shell",
        "severity": "high",
        "description": "Command pipes to a shell interpreter (remote code execution pattern)",
        "match": r"\|\s*(ba)?sh\b|\|\s*(ba)?sh\s*$|\|\s*python\b|\|\s*perl\b",
        "window_s": 300,
        "threshold": 1,
        "message": "Pipe-to-shell detected: '{}'",
    },
    {
        "name": "curl-pipe-shell",
        "severity": "critical",
        "description": "curl/wget piped straight into a shell — classic supply-chain RCE",
        "match": r"(curl|wget)\s+[^\|]*\|\s*(ba)?sh\b",
        "window_s": 300,
        "threshold": 1,
        "message": "curl|sh supply-chain pattern: '{}'",
    },
    {
        "name": "secret-in-command",
        "severity": "high",
        "description": "Likely secret material on the command line (token, key, password)",
        "match": r"(?i)(token|secret|password|passwd|api[_-]?key|auth)\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{12,}",
        "window_s": None,
        "threshold": 1,
        "message": "Possible secret on command line: '{}'",
    },
    {
        "name": "privilege-escalation",
        "severity": "medium",
        "description": "Privilege escalation or user switch",
        "match": r"(?i)\bsudo\s+(-i|-s|su)\b|\bsu\s+-\s+\w+\b|\bsudo\s+su\b",
        "window_s": None,
        "threshold": 1,
        "message": "Privilege escalation: '{}'",
    },
    {
        "name": "destructive-command",
        "severity": "critical",
        "description": "Destructive or dangerous command",
        "match": r"(?i)\brm\s+-rf\s+/\b|\bdd\s+if=.*of=/dev/\b|:\(\)\s*\{\s*:\|:&\s*\}\s*;",
        "window_s": None,
        "threshold": 1,
        "message": "Destructive command: '{}'",
    },
    {
        "name": "exfiltration-pattern",
        "severity": "high",
        "description": "Potential data exfiltration (reverse shell / encoded payload)",
        "match": r"(?i)\bnc\s+-[a-z]*e\b|\bbash\s+-i\s*>\s*&\s*/dev/tcp/|base64\s+-d.*\|\s*(ba)?sh\b",
        "window_s": None,
        "threshold": 1,
        "message": "Exfiltration/reverse-shell pattern: '{}'",
    },
    {
        "name": "auth-failure-spike",
        "severity": "medium",
        "description": "Multiple authentication failures in a short window",
        "match": r"(?i)(permission denied|authentication failed|access denied|sudo:.*incorrect password|ssh.*(denied|refused))",
        "window_s": 600,
        "threshold": 3,
        "message": "{} auth failures in 10 minutes",
    },
    {
        "name": "long-running-command",
        "severity": "info",
        "description": "Command ran unusually long (possible hang or heavy job)",
        "match": r".*",
        "window_s": None,
        "threshold": 1,
        "min_duration_ms": 300_000,
        "message": "Long-running command (>{min_duration_ms/1000:.0f}s): '{}'",
    },
    {
        "name": "kill-process-family",
        "severity": "medium",
        "description": "Bulk process kill — possible service disruption",
        "match": r"(?i)\b(killall|pkill)\b.*\b(-9|--signal\s+9)\b",
        "window_s": None,
        "threshold": 1,
        "message": "Force-kill of processes: '{}'",
    },
    {
        "name": "suspicious-download",
        "severity": "medium",
        "description": "Downloading an executable or archive from the internet",
        "match": r"(?i)\b(wget|curl)\s+.*\.(exe|msi|sh|bin|deb|rpm|appimage)\b",
        "window_s": None,
        "threshold": 1,
        "message": "Executable download: '{}'",
    },
]

# ---------------------------------------------------------------------------
# Config load / merge
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("~/.config/retrace/detectors.json").expanduser()


def load_rules(config_path: Path | None = None) -> list[dict[str, Any]]:
    """Merge user rules over built-ins. User can disable (enabled:false)
    or override any rule by matching name; can also add new rules."""
    rules = {r["name"]: dict(r) for r in BUILTIN_RULES}
    path = config_path or DEFAULT_CONFIG_PATH
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            for item in user:
                name = item.get("name")
                if not name:
                    continue
                if item.get("enabled") is False:
                    rules.pop(name, None)
                    continue
                base = rules.get(name, {})
                base.update(item)
                rules[name] = base
        except (json.JSONDecodeError, OSError) as exc:
            # Never let a bad config break the whole engine.
            print(f"[detectors] warning: could not load {path}: {exc}")
    return list(rules.values())


def _compile(rule: dict[str, Any]) -> re.Pattern | None:
    try:
        return re.compile(rule["match"], re.IGNORECASE)
    except re.error:
        return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    rows: list[dict[str, Any]],
    rules: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Run all rules over command rows. Returns list of alert dicts:

        {rule, severity, message, count, first_ts, last_ts, samples}
    """
    rules = rules if rules is not None else load_rules()
    now = now if now is not None else time.time()
    alerts: list[dict[str, Any]] = []

    for rule in rules:
        pattern = _compile(rule)
        if pattern is None:
            continue
        window_s = rule.get("window_s")
        threshold = rule.get("threshold", 1)
        min_duration_ms = rule.get("min_duration_ms")

        hits: list[dict[str, Any]] = []
        for row in rows:
            cmd = row.get("command") or ""
            if not pattern.search(cmd):
                continue
            if min_duration_ms is not None:
                dur = row.get("duration_ms") or 0
                if dur < min_duration_ms:
                    continue
            if window_s is not None:
                if (now - (row.get("ts") or now)) > window_s:
                    continue
            hits.append(row)

        if len(hits) >= threshold:
            try:
                message = rule["message"].format(hits[0].get("command", ""), count=len(hits))
            except (KeyError, IndexError):
                message = rule.get("message", rule["name"])
            alerts.append(
                {
                    "rule": rule["name"],
                    "severity": rule.get("severity", "info"),
                    "message": message,
                    "count": len(hits),
                    "first_ts": min((h.get("ts") or now) for h in hits),
                    "last_ts": max((h.get("ts") or now) for h in hits),
                    "samples": [h.get("command", "") for h in hits[:5]],
                }
            )

    # Sort: critical first, then high/medium/info, then by recency
    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    alerts.sort(key=lambda a: (order.get(a["severity"], 9), -a["last_ts"]))
    return alerts


def detect(conn, since_minutes: int = 60, limit: int = 2000) -> list[dict[str, Any]]:
    """Pull recent rows from the DB and run the rule engine."""
    since = time.time() - since_minutes * 60
    rows = conn.execute(
        "SELECT ts, command, duration_ms, source, shell FROM commands "
        "WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    cols = ["ts", "command", "duration_ms", "source", "shell"]
    return evaluate([dict(zip(cols, r)) for r in rows])


def write_default_config(path: Path | None = None) -> Path:
    """Write a commented example config so users can extend rules."""
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "example-custom-rule",
                        "severity": "medium",
                        "description": "Example: flag any use of tmux",
                        "match": r"\btmux\b",
                        "window_s": None,
                        "threshold": 1,
                        "message": "tmux used: '{}'",
                    }
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return path