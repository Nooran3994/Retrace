"""Analytics engine for Retrace — deterministic SQL aggregations.

Pure read-only queries over the append-only SQLite store. No writes,
no schema changes, no external calls. Every function returns plain
JSON-serializable dicts/lists so the web UI, reports, and CLI can all
consume the same shapes.

Design rules:
  - All bucket/column names are whitelisted (never interpolated from
    user input); values always go through ?-parameters.
  - Missing time buckets are zero-filled so charts render continuous
    series without client-side gap handling.
  - Empty databases return zeroed shapes, never exceptions.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

# SQLite strftime buckets — keyed by name, value is the format string.
_BUCKET_FMT = {
    "hour": "%Y-%m-%d %H:00",
    "day": "%Y-%m-%d",
    "week": "%Y-%W",
    "month": "%Y-%m",
}

# Whitelist for ORDER BY / GROUP BY column names (anti-injection).
_SORTABLE = {"count", "ts", "duration_ms", "last_ts"}


def _now() -> float:
    return time.time()


def _bucket_start(ts: float, bucket: str) -> str:
    """Format a timestamp into its bucket label (UTC)."""
    fmt = _BUCKET_FMT.get(bucket, _BUCKET_FMT["hour"])
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(fmt)


def _zero_fill(bucket: str, start: float, end: float) -> dict[str, int]:
    """Build a dict of every bucket label in [start, end] -> 0."""
    out: dict[str, int] = {}
    if bucket == "hour":
        cur = datetime.fromtimestamp(start, tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
        stop = datetime.fromtimestamp(end, tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
        while cur <= stop:
            out[cur.strftime(_BUCKET_FMT["hour"])] = 0
            cur += timedelta(hours=1)
    elif bucket == "day":
        cur = datetime.fromtimestamp(start, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        stop = datetime.fromtimestamp(end, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        while cur <= stop:
            out[cur.strftime(_BUCKET_FMT["day"])] = 0
            cur += timedelta(days=1)
    elif bucket == "week":
        cur = datetime.fromtimestamp(start, tz=timezone.utc)
        stop = datetime.fromtimestamp(end, tz=timezone.utc)
        while cur <= stop:
            out[cur.strftime(_BUCKET_FMT["week"])] = 0
            cur += timedelta(days=7)
    elif bucket == "month":
        cur = datetime.fromtimestamp(start, tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        stop = datetime.fromtimestamp(end, tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= stop:
            out[cur.strftime(_BUCKET_FMT["month"])] = 0
            cur += timedelta(days=32)
            cur = cur.replace(day=1)
    return out


def _series(conn, bucket: str, start: float, end: float) -> dict[str, int]:
    """Count commands per bucket over [start, end), zero-filled."""
    fmt = _BUCKET_FMT[bucket]
    rows = conn.execute(
        f"SELECT strftime('{fmt}', ts, 'unixepoch') AS b, COUNT(*) "
        "FROM commands WHERE ts >= ? AND ts < ? GROUP BY b ORDER BY b",
        (start, end),
    ).fetchall()
    filled = _zero_fill(bucket, start, end)
    for b, n in rows:
        filled[b] = n
    return filled


def _alert_series(conn, bucket: str, start: float, end: float) -> dict[str, int]:
    """Count alerts per bucket over [start, end), zero-filled."""
    fmt = _BUCKET_FMT[bucket]
    rows = conn.execute(
        f"SELECT strftime('{fmt}', ts, 'unixepoch') AS b, COUNT(*) "
        "FROM alerts WHERE ts >= ? AND ts < ? GROUP BY b ORDER BY b",
        (start, end),
    ).fetchall()
    filled = _zero_fill(bucket, start, end)
    for b, n in rows:
        filled[b] = n
    return filled


def _first_token(command: str) -> str:
    """Extract the command name (first shell word), lowercased."""
    cmd = (command or "").strip()
    if not cmd:
        return "(empty)"
    # Skip leading env assignments and prefixes like `sudo`, `env`.
    parts = cmd.split()
    for part in parts:
        if "=" in part and not part.startswith(("-", "/")):
            continue
        if part in ("sudo", "env", "nohup", "time", "command"):
            continue
        # Strip path prefix and common suffixes.
        base = part.split("/")[-1].lstrip("-")
        return base.lower() or "(empty)"
    return parts[0].split("/")[-1].lower() or "(empty)"


# ---------------------------------------------------------------------------
# Public analytics API
# ---------------------------------------------------------------------------


def overview(conn) -> dict[str, Any]:
    """Top-level dashboard numbers."""
    total = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    first_ts = conn.execute("SELECT MIN(ts) FROM commands").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(ts) FROM commands").fetchone()[0]
    hosts = conn.execute("SELECT COUNT(DISTINCT host) FROM commands WHERE host IS NOT NULL AND host != ''").fetchone()[0]
    sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM commands WHERE session_id IS NOT NULL AND session_id != ''").fetchone()[0]
    alerts_open = conn.execute("SELECT COUNT(*) FROM alerts WHERE acked=0").fetchone()[0]
    alerts_total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    failures = conn.execute("SELECT COUNT(*) FROM commands WHERE exit_code IS NOT NULL AND exit_code != 0").fetchone()[0]
    with_exit = conn.execute("SELECT COUNT(*) FROM commands WHERE exit_code IS NOT NULL").fetchone()[0]

    by_source = dict(conn.execute("SELECT source, COUNT(*) FROM commands GROUP BY source").fetchall())
    by_shell = dict(conn.execute("SELECT shell, COUNT(*) FROM commands GROUP BY shell ORDER BY 2 DESC").fetchall())

    return {
        "total": total,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "hosts": hosts,
        "sessions": sessions,
        "alerts_open": alerts_open,
        "alerts_total": alerts_total,
        "failures": failures,
        "failure_rate": round((failures / with_exit * 100), 2) if with_exit else 0.0,
        "by_source": by_source,
        "by_shell": by_shell,
    }


def time_series(conn, bucket: str = "day", since_days: int = 30) -> list[dict[str, Any]]:
    """Commands + alerts per bucket. Returns [{bucket, commands, alerts}]."""
    if bucket not in _BUCKET_FMT:
        bucket = "day"
    end = _now()
    start = end - since_days * 86400
    cmds = _series(conn, bucket, start, end)
    alrts = _alert_series(conn, bucket, start, end)
    return [
        {"bucket": label, "commands": cmds.get(label, 0), "alerts": alrts.get(label, 0)}
        for label in cmds
    ]


def top_commands(conn, limit: int = 20, since_days: int = 30) -> list[dict[str, Any]]:
    """Most frequent raw commands (exact text)."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT command, COUNT(*) AS n FROM commands "
        "WHERE ts >= ? AND command != '' GROUP BY command ORDER BY n DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [{"command": r[0], "count": r[1]} for r in rows]


def command_usage(conn, limit: int = 25, since_days: int = 30) -> list[dict[str, Any]]:
    """Distribution by command name (first token), normalized."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT command FROM commands WHERE ts >= ? AND command != ''",
        (since,),
    ).fetchall()
    counter: Counter[str] = Counter()
    for (cmd,) in rows:
        counter[_first_token(cmd)] += 1
    total = sum(counter.values())
    out = []
    for name, n in counter.most_common(limit):
        out.append({
            "name": name,
            "count": n,
            "share": round(n / total * 100, 2) if total else 0.0,
        })
    return {"total": total, "items": out}


def top_directories(conn, limit: int = 10, since_days: int = 30) -> list[dict[str, Any]]:
    """Most active working directories."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT cwd, COUNT(*) AS n FROM commands "
        "WHERE ts >= ? AND cwd IS NOT NULL AND cwd != '' "
        "GROUP BY cwd ORDER BY n DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [{"cwd": r[0], "count": r[1]} for r in rows]


def git_activity(conn, limit: int = 10, since_days: int = 30) -> dict[str, Any]:
    """Repos touched, branches, dirty-state counts."""
    since = _now() - since_days * 86400
    repos = conn.execute(
        "SELECT git_repo, COUNT(*) AS n FROM commands "
        "WHERE ts >= ? AND git_repo IS NOT NULL AND git_repo != '' "
        "GROUP BY git_repo ORDER BY n DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    dirty = conn.execute(
        "SELECT COUNT(*) FROM commands WHERE ts >= ? AND git_dirty = 1",
        (since,),
    ).fetchone()[0]
    return {
        "repos": [{"repo": r[0], "count": r[1]} for r in repos],
        "dirty_commands": dirty,
    }


def failure_stats(conn, since_days: int = 30) -> dict[str, Any]:
    """Exit-code distribution and failure rate."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT exit_code, COUNT(*) FROM commands "
        "WHERE ts >= ? AND exit_code IS NOT NULL GROUP BY exit_code ORDER BY 2 DESC",
        (since,),
    ).fetchall()
    total = sum(n for _, n in rows)
    non_zero = sum(n for code, n in rows if code != 0)
    return {
        "total_with_exit": total,
        "failures": non_zero,
        "failure_rate": round(non_zero / total * 100, 2) if total else 0.0,
        "by_code": [{"exit_code": c, "count": n} for c, n in rows],
    }


def duration_stats(conn, since_days: int = 30) -> dict[str, Any]:
    """Command duration percentiles (ms)."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT duration_ms FROM commands "
        "WHERE ts >= ? AND duration_ms IS NOT NULL AND duration_ms >= 0",
        (since,),
    ).fetchall()
    vals = sorted(r[0] for r in rows)
    n = len(vals)
    if n == 0:
        return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0}

    def pct(p: float) -> int:
        return int(vals[min(n - 1, int(n * p))])

    return {
        "count": n,
        "avg_ms": int(sum(vals) / n),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": vals[-1],
    }


def session_stats(conn, since_days: int = 30) -> dict[str, Any]:
    """Per-session activity: count, commands/session, span."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT session_id, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
        "FROM commands WHERE ts >= ? AND session_id IS NOT NULL AND session_id != '' "
        "GROUP BY session_id",
        (since,),
    ).fetchall()
    if not rows:
        return {"sessions": 0, "avg_commands": 0, "avg_span_min": 0}
    sessions = len(rows)
    avg_cmds = sum(r[1] for r in rows) / sessions
    spans = [(r[3] - r[2]) / 60 for r in rows if r[3] and r[2]]
    avg_span = (sum(spans) / len(spans)) if spans else 0.0
    return {
        "sessions": sessions,
        "avg_commands": round(avg_cmds, 1),
        "avg_span_min": round(avg_span, 1),
    }


def alert_summary(conn, since_days: int = 30) -> dict[str, Any]:
    """Alerts by rule and by severity."""
    since = _now() - since_days * 86400
    by_rule = conn.execute(
        "SELECT rule, COUNT(*) FROM alerts WHERE ts >= ? GROUP BY rule ORDER BY 2 DESC",
        (since,),
    ).fetchall()
    by_severity = conn.execute(
        "SELECT severity, COUNT(*) FROM alerts WHERE ts >= ? GROUP BY severity ORDER BY 2 DESC",
        (since,),
    ).fetchall()
    return {
        "by_rule": [{"rule": r[0], "count": r[1]} for r in by_rule],
        "by_severity": dict(by_severity),
    }


def hourly_profile(conn, since_days: int = 30) -> list[dict[str, Any]]:
    """Commands per hour-of-day (0-23) — for the activity bar chart."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT CAST(strftime('%H', ts, 'unixepoch') AS INTEGER) AS h, COUNT(*) "
        "FROM commands WHERE ts >= ? GROUP BY h",
        (since,),
    ).fetchall()
    by_hour = dict(rows)
    return [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)]


def weekday_profile(conn, since_days: int = 90) -> list[dict[str, Any]]:
    """Commands per day-of-week (0=Mon..6=Sun)."""
    since = _now() - since_days * 86400
    rows = conn.execute(
        "SELECT CAST(strftime('%w', ts, 'unixepoch') AS INTEGER) AS d, COUNT(*) "
        "FROM commands WHERE ts >= ? GROUP BY d",
        (since,),
    ).fetchall()
    by_day = dict(rows)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [{"day": names[d], "count": by_day.get(d, 0)} for d in range(7)]


def activity_heatmap(conn, days: int = 30) -> dict[str, Any]:
    """24h x days grid: {days: N, cells: [{date, hour, count}]}."""
    end = _now()
    start = end - days * 86400
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS d, "
        "CAST(strftime('%H', ts, 'unixepoch') AS INTEGER) AS h, COUNT(*) "
        "FROM commands WHERE ts >= ? AND ts < ? GROUP BY d, h",
        (start, end),
    ).fetchall()
    return {
        "days": days,
        "cells": [{"date": d, "hour": h, "count": n} for d, h, n in rows],
    }


def entropy_scan(conn, since_days: int = 30) -> dict[str, Any]:
    """High-entropy (potential secret) flags."""
    since = _now() - since_days * 86400
    n = conn.execute("SELECT COUNT(*) FROM commands WHERE ts >= ? AND entropy = 1", (since,)).fetchone()[0]
    recent = conn.execute(
        "SELECT ts, command FROM commands WHERE ts >= ? AND entropy = 1 ORDER BY ts DESC LIMIT 10",
        (since,),
    ).fetchall()
    return {
        "flagged": n,
        "recent": [{"ts": r[0], "command": r[1]} for r in recent],
    }


def range_query(
    conn,
    since_days: int = 30,
    source: str | None = None,
    shell: str | None = None,
    host: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Filtered record listing for reports/export. Whitelisted filters only."""
    since = _now() - since_days * 86400
    sql = "SELECT ts, source, shell, command, cwd, exit_code, duration_ms, host, git_repo, git_branch FROM commands WHERE ts >= ?"
    params: list[Any] = [since]
    if source:
        sql += " AND source = ?"
        params.append(source)
    if shell:
        sql += " AND shell = ?"
        params.append(shell)
    if host:
        sql += " AND host = ?"
        params.append(host)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    cols = ["ts", "source", "shell", "command", "cwd", "exit_code", "duration_ms", "host", "git_repo", "git_branch"]
    return [dict(zip(cols, r)) for r in rows]


def full_report(conn, since_days: int = 30) -> dict[str, Any]:
    """Everything the report generator and dashboard need, in one call."""
    return {
        "generated_at": _now(),
        "since_days": since_days,
        "overview": overview(conn),
        "series": time_series(conn, bucket="day", since_days=since_days),
        "top_commands": top_commands(conn, limit=20, since_days=since_days),
        "usage": command_usage(conn, limit=25, since_days=since_days),
        "dirs": top_directories(conn, limit=10, since_days=since_days),
        "git": git_activity(conn, limit=10, since_days=since_days),
        "failures": failure_stats(conn, since_days=since_days),
        "durations": duration_stats(conn, since_days=since_days),
        "sessions": session_stats(conn, since_days=since_days),
        "alerts": alert_summary(conn, since_days=since_days),
        "hourly": hourly_profile(conn, since_days=since_days),
        "weekday": weekday_profile(conn, since_days=since_days),
        "heatmap": activity_heatmap(conn, days=since_days),
        "entropy": entropy_scan(conn, since_days=since_days),
    }