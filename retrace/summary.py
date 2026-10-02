"""Materialized summary tables for Retrace — instant analytics at any DB size.

Problem: the dashboard and reports run 5-10 full-table GROUP BY aggregations
over the raw ``commands`` table. At ~1M rows that is 10-20s per cold load.

Fix: pre-aggregate commands into per-hour rollup tables, refreshed
incrementally (only rows newer than the last processed timestamp are folded
in). After the one-time backfill, every dashboard/report query reads only
the small summary tables — milliseconds at any scale.

Design (dimension-specific rollups):
  summary_ts      (hour)                     -> count           : series, hourly, weekday, heatmap, global total
  summary_usage   (hour, cmd_name)           -> count           : command usage distribution
  summary_src     (hour, source)             -> count           : by-source
  summary_shell   (hour, shell)              -> count           : by-shell
  summary_exit    (hour, exit_code)          -> count           : failures / exit-code distribution
  summary_ent     (hour, entropy)            -> count           : entropy flags
  summary_dir     (hour, cwd)                -> count           : working directories
  summary_git     (hour, repo, branch, dirty)-> count           : git activity
  summary_dur     (hour, bucket)             -> count           : duration histogram (50ms buckets)
  summary_cmd_day (day, command)             -> count           : exact top commands (pruned ~400 days)

Each table's leading column is ``hour`` so window scans use the PK prefix
and stay tiny. Rows collapse hard: 200k commands in 30 days -> ~720 hour
rows for series, a few thousand for usage — not 158k.

Same shapes: the summary_* query functions return the exact dict/list
shapes the dashboard and reports already consume, so webui/reports switch
over with zero client changes.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .analytics import _first_token, _zero_fill

_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS summary_ts (
    hour    INTEGER PRIMARY KEY,
    count   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS summary_usage (
    hour     INTEGER NOT NULL,
    cmd_name TEXT NOT NULL DEFAULT '',
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, cmd_name)
);
CREATE TABLE IF NOT EXISTS summary_src (
    hour    INTEGER NOT NULL,
    source  TEXT NOT NULL DEFAULT '',
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, source)
);
CREATE TABLE IF NOT EXISTS summary_shell (
    hour    INTEGER NOT NULL,
    shell   TEXT NOT NULL DEFAULT '',
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, shell)
);
CREATE TABLE IF NOT EXISTS summary_exit (
    hour      INTEGER NOT NULL,
    exit_code INTEGER NOT NULL DEFAULT -1,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, exit_code)
);
CREATE TABLE IF NOT EXISTS summary_ent (
    hour    INTEGER NOT NULL,
    entropy INTEGER NOT NULL DEFAULT 0,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, entropy)
);
CREATE TABLE IF NOT EXISTS summary_dir (
    hour    INTEGER NOT NULL,
    cwd     TEXT NOT NULL DEFAULT '',
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, cwd)
);
CREATE TABLE IF NOT EXISTS summary_git (
    hour      INTEGER NOT NULL,
    repo      TEXT NOT NULL DEFAULT '',
    branch    TEXT NOT NULL DEFAULT '',
    dirty     INTEGER NOT NULL DEFAULT 0,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, repo, branch, dirty)
);
CREATE TABLE IF NOT EXISTS summary_dur (
    hour    INTEGER NOT NULL,
    bucket  INTEGER NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, bucket)
);
CREATE TABLE IF NOT EXISTS summary_cmd_day (
    day     TEXT NOT NULL,
    command TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, command)
);
CREATE TABLE IF NOT EXISTS summary_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_EXIT_NULL = -1          # sentinel for NULL exit_code
_DUR_BUCKET_MS = 50      # histogram bucket width (ms)
_DUR_MAX_BUCKET = 20000  # cap: 1,000,000 ms
_CMD_RETAIN_DAYS = 400   # bound summary_cmd_day growth

_lock = threading.Lock()


def ensure_schema(conn) -> None:
    """Create summary tables if missing (idempotent)."""
    conn.executescript(_SUMMARY_SCHEMA)
    conn.commit()


def last_processed(conn) -> float:
    row = conn.execute("SELECT value FROM summary_meta WHERE key='last_ts'").fetchone()
    return float(row[0]) if row else 0.0


def _set_last(conn, ts: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO summary_meta (key, value) VALUES ('last_ts', ?)",
        (str(ts),),
    )
    conn.commit()


def refresh_summaries(conn, force: bool = False) -> int:
    """Fold new commands into the rollup tables. Returns hour-rows written.

    Safe on every dashboard request: if nothing new arrived it is a no-op
    (single MAX(ts) query). Guarded by a process-wide lock so concurrent
    requests never double-apply the same rows.
    """
    ensure_schema(conn)
    with _lock:
        last = 0.0 if force else last_processed(conn)
        max_ts = conn.execute("SELECT MAX(ts) FROM commands").fetchone()[0] or 0.0
        if max_ts <= last:
            return 0

        cur = conn.execute(
            "SELECT ts, source, shell, command, cwd, exit_code, duration_ms, "
            "git_repo, git_branch, git_dirty, entropy "
            "FROM commands WHERE ts > ?",
            (last,),
        )

        ts_agg: dict[int, int] = {}
        usage_agg: dict[tuple, int] = {}
        src_agg: dict[tuple, int] = {}
        shell_agg: dict[tuple, int] = {}
        exit_agg: dict[tuple, int] = {}
        ent_agg: dict[tuple, int] = {}
        dir_agg: dict[tuple, int] = {}
        git_agg: dict[tuple, int] = {}
        dur_agg: dict[tuple, int] = {}
        cmd_day_agg: dict[tuple, int] = {}

        for ts, source, shell, command, cwd, exit_code, dur_ms, repo, branch, dirty, entropy in cur:
            hour = int(ts // 3600)
            src = source or ""
            sh = shell or ""
            name = _first_token(command or "")
            cw = cwd or ""
            code = _EXIT_NULL if exit_code is None else int(exit_code)
            repo = repo or ""
            branch = branch or ""
            d = 1 if dirty else 0
            ent = 1 if entropy else 0

            ts_agg[hour] = ts_agg.get(hour, 0) + 1
            uk = (hour, name)
            usage_agg[uk] = usage_agg.get(uk, 0) + 1
            sk = (hour, src)
            src_agg[sk] = src_agg.get(sk, 0) + 1
            shk = (hour, sh)
            shell_agg[shk] = shell_agg.get(shk, 0) + 1
            ek = (hour, code)
            exit_agg[ek] = exit_agg.get(ek, 0) + 1
            entk = (hour, ent)
            ent_agg[entk] = ent_agg.get(entk, 0) + 1
            dk = (hour, cw)
            dir_agg[dk] = dir_agg.get(dk, 0) + 1
            gk = (hour, repo, branch, d)
            git_agg[gk] = git_agg.get(gk, 0) + 1

            if dur_ms is not None and dur_ms >= 0:
                bucket = min(int(dur_ms // _DUR_BUCKET_MS), _DUR_MAX_BUCKET)
                duk = (hour, bucket)
                dur_agg[duk] = dur_agg.get(duk, 0) + 1

            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            ck = (day, command or "")
            cmd_day_agg[ck] = cmd_day_agg.get(ck, 0) + 1

        def upsert(table, cols, data, conflict_cols):
            if not data:
                return
            placeholders = ",".join("?" * (len(cols) + 1))
            conflict = ",".join(conflict_cols)
            rows = []
            for k, v in data.items():
                key = k if isinstance(k, tuple) else (k,)
                rows.append((*key, v))
            conn.executemany(
                f"INSERT INTO {table} ({','.join(cols)}, count) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict}) DO UPDATE SET count=count+excluded.count",
                rows,
            )

        upsert("summary_ts", ["hour"], ts_agg, ["hour"])
        upsert("summary_usage", ["hour", "cmd_name"], usage_agg, ["hour", "cmd_name"])
        upsert("summary_src", ["hour", "source"], src_agg, ["hour", "source"])
        upsert("summary_shell", ["hour", "shell"], shell_agg, ["hour", "shell"])
        upsert("summary_exit", ["hour", "exit_code"], exit_agg, ["hour", "exit_code"])
        upsert("summary_ent", ["hour", "entropy"], ent_agg, ["hour", "entropy"])
        upsert("summary_dir", ["hour", "cwd"], dir_agg, ["hour", "cwd"])
        upsert("summary_git", ["hour", "repo", "branch", "dirty"], git_agg, ["hour", "repo", "branch", "dirty"])
        upsert("summary_dur", ["hour", "bucket"], dur_agg, ["hour", "bucket"])
        upsert("summary_cmd_day", ["day", "command"], cmd_day_agg, ["day", "command"])

        cutoff = (datetime.now(timezone.utc) - timedelta(days=_CMD_RETAIN_DAYS)).strftime("%Y-%m-%d")
        conn.execute("DELETE FROM summary_cmd_day WHERE day < ?", (cutoff,))

        _set_last(conn, max_ts)
        return len(ts_agg)


# ---------------------------------------------------------------------------
# Window helper
# ---------------------------------------------------------------------------


def _window_hours(since_days: int) -> tuple[int, int]:
    end = time.time()
    start = end - since_days * 86400
    return int(start // 3600), int(end // 3600)


# ---------------------------------------------------------------------------
# Summary-backed queries (same shapes as retrace/analytics.py)
# ---------------------------------------------------------------------------


def summary_overview(conn, since_days: int = 30) -> dict[str, Any]:
    """Global overview (no window on totals, matching analytics.overview)."""
    total = conn.execute("SELECT COALESCE(SUM(count),0) FROM summary_ts").fetchone()[0]
    by_source = dict(
        conn.execute(
            "SELECT source, SUM(count) FROM summary_src WHERE source!='' "
            "GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
    )
    by_shell = dict(
        conn.execute(
            "SELECT shell, SUM(count) FROM summary_shell WHERE shell!='' "
            "GROUP BY shell ORDER BY 2 DESC"
        ).fetchall()
    )
    failures = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_exit WHERE exit_code>0"
    ).fetchone()[0]
    with_exit = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_exit WHERE exit_code>=-1"
    ).fetchone()[0]
    entropy = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_ent WHERE entropy=1"
    ).fetchone()[0]
    hosts = conn.execute(
        "SELECT COUNT(DISTINCT host) FROM commands WHERE host IS NOT NULL AND host!=''"
    ).fetchone()[0]
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM commands "
        "WHERE session_id IS NOT NULL AND session_id!=''"
    ).fetchone()[0]
    alerts_open = conn.execute("SELECT COUNT(*) FROM alerts WHERE acked=0").fetchone()[0]
    alerts_total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    first_ts = conn.execute("SELECT MIN(ts) FROM commands").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(ts) FROM commands").fetchone()[0]
    return {
        "total": total,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "hosts": hosts,
        "sessions": sessions,
        "alerts_open": alerts_open,
        "alerts_total": alerts_total,
        "failures": failures,
        "failure_rate": round(failures / with_exit * 100, 2) if with_exit else 0.0,
        "by_source": by_source,
        "by_shell": by_shell,
        "entropy_flagged": entropy,
    }


def summary_series(conn, bucket: str = "day", since_days: int = 30) -> list[dict[str, Any]]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%d', hour*3600, 'unixepoch') AS d, SUM(count) "
        "FROM summary_ts WHERE hour>=? AND hour<? GROUP BY d ORDER BY d",
        (h0, h1),
    ).fetchall()
    filled = _zero_fill("day", h0 * 3600, h1 * 3600)
    for d, n in rows:
        filled[d] = n
    alrts = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS d, COUNT(*) "
        "FROM alerts WHERE ts>=? AND ts<? GROUP BY d",
        (h0 * 3600, h1 * 3600),
    ).fetchall()
    a_filled = _zero_fill("day", h0 * 3600, h1 * 3600)
    for d, n in alrts:
        a_filled[d] = n
    return [
        {"bucket": k, "commands": filled.get(k, 0), "alerts": a_filled.get(k, 0)}
        for k in filled
    ]


def summary_hourly(conn, since_days: int = 30) -> list[dict[str, Any]]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT hour%24 AS h, SUM(count) FROM summary_ts "
        "WHERE hour>=? AND hour<? GROUP BY h",
        (h0, h1),
    ).fetchall()
    by_hour = dict(rows)
    return [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)]


def summary_weekday(conn, since_days: int = 90) -> list[dict[str, Any]]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT CAST(strftime('%w', hour*3600, 'unixepoch') AS INTEGER) AS d, SUM(count) "
        "FROM summary_ts WHERE hour>=? AND hour<? GROUP BY d",
        (h0, h1),
    ).fetchall()
    by_day = dict(rows)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [{"day": names[d], "count": by_day.get(d, 0)} for d in range(7)]


def summary_usage(conn, limit: int = 25, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    total = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_usage WHERE hour>=? AND hour<? AND cmd_name!=''",
        (h0, h1),
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT cmd_name, SUM(count) AS n FROM summary_usage "
        "WHERE hour>=? AND hour<? AND cmd_name!='' GROUP BY cmd_name ORDER BY n DESC LIMIT ?",
        (h0, h1, limit),
    ).fetchall()
    items = [
        {"name": name, "count": n, "share": round(n / total * 100, 2) if total else 0.0}
        for name, n in rows
    ]
    return {"total": total, "items": items}


def summary_top_commands(conn, limit: int = 20, since_days: int = 30) -> list[dict[str, Any]]:
    cutoff_day = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT command, SUM(count) AS n FROM summary_cmd_day "
        "WHERE day>=? AND command!='' GROUP BY command ORDER BY n DESC LIMIT ?",
        (cutoff_day, limit),
    ).fetchall()
    return [{"command": c, "count": n} for c, n in rows]


def summary_dirs(conn, limit: int = 10, since_days: int = 30) -> list[dict[str, Any]]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT cwd, SUM(count) AS n FROM summary_dir "
        "WHERE hour>=? AND hour<? AND cwd!='' GROUP BY cwd ORDER BY n DESC LIMIT ?",
        (h0, h1, limit),
    ).fetchall()
    return [{"cwd": c, "count": n} for c, n in rows]


def summary_git(conn, limit: int = 10, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    repos = conn.execute(
        "SELECT repo, branch, SUM(count) AS n FROM summary_git "
        "WHERE hour>=? AND hour<? AND repo!='' "
        "GROUP BY repo, branch ORDER BY n DESC LIMIT ?",
        (h0, h1, limit),
    ).fetchall()
    dirty = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_git "
        "WHERE hour>=? AND hour<? AND dirty=1",
        (h0, h1),
    ).fetchone()[0]
    return {
        "repos": [{"repo": r, "branch": b, "count": n} for r, b, n in repos],
        "dirty_commands": dirty,
    }


def summary_failures(conn, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT exit_code, SUM(count) AS n FROM summary_exit "
        "WHERE hour>=? AND hour<? GROUP BY exit_code ORDER BY n DESC",
        (h0, h1),
    ).fetchall()
    items = [{"exit_code": None if c == _EXIT_NULL else c, "count": n} for c, n in rows]
    total = sum(n for _, n in rows)
    non_zero = sum(n for c, n in rows if c > 0)
    return {
        "total_with_exit": total,
        "failures": non_zero,
        "failure_rate": round(non_zero / total * 100, 2) if total else 0.0,
        "by_code": items,
    }


def summary_durations(conn, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    rows = conn.execute(
        "SELECT bucket, SUM(count) FROM summary_dur "
        "WHERE hour>=? AND hour<? GROUP BY bucket ORDER BY bucket",
        (h0, h1),
    ).fetchall()
    total = sum(n for _, n in rows)
    if total == 0:
        return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0}

    def pct(p: float) -> int:
        target = p * total
        acc = 0
        for bucket, n in rows:
            acc += n
            if acc >= target:
                return bucket * _DUR_BUCKET_MS
        return rows[-1][0] * _DUR_BUCKET_MS

    avg = sum(bucket * _DUR_BUCKET_MS * n for bucket, n in rows) / total
    return {
        "count": total,
        "avg_ms": int(avg),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": rows[-1][0] * _DUR_BUCKET_MS,
    }


def summary_sessions(conn, since_days: int = 30) -> dict[str, Any]:
    from .analytics import session_stats

    return session_stats(conn, since_days=since_days)


def summary_entropy(conn, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    n = conn.execute(
        "SELECT COALESCE(SUM(count),0) FROM summary_ent "
        "WHERE hour>=? AND hour<? AND entropy=1",
        (h0, h1),
    ).fetchone()[0]
    recent = conn.execute(
        "SELECT ts, command FROM commands "
        "WHERE ts>=? AND ts<? AND entropy=1 ORDER BY ts DESC LIMIT 10",
        (h0 * 3600, h1 * 3600),
    ).fetchall()
    return {"flagged": n, "recent": [{"ts": t, "command": c} for t, c in recent]}


def summary_alert_summary(conn, since_days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(since_days)
    by_rule = conn.execute(
        "SELECT rule, COUNT(*) FROM alerts WHERE ts>=? AND ts<? GROUP BY rule ORDER BY 2 DESC",
        (h0 * 3600, h1 * 3600),
    ).fetchall()
    by_severity = conn.execute(
        "SELECT severity, COUNT(*) FROM alerts WHERE ts>=? AND ts<? GROUP BY severity ORDER BY 2 DESC",
        (h0 * 3600, h1 * 3600),
    ).fetchall()
    return {
        "by_rule": [{"rule": r, "count": n} for r, n in by_rule],
        "by_severity": dict(by_severity),
    }


def summary_heatmap(conn, days: int = 30) -> dict[str, Any]:
    h0, h1 = _window_hours(days)
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%d', hour*3600, 'unixepoch') AS d, hour%24 AS h, SUM(count) "
        "FROM summary_ts WHERE hour>=? AND hour<? GROUP BY d, h",
        (h0, h1),
    ).fetchall()
    return {"days": days, "cells": [{"date": d, "hour": h, "count": n} for d, h, n in rows]}


# ---------------------------------------------------------------------------
# Aggregated payloads (drop-in for analytics.full_report / dashboard)
# ---------------------------------------------------------------------------


def summary_dashboard(conn, days: int = 30) -> dict[str, Any]:
    return {
        "overview": summary_overview(conn, since_days=days),
        "series": summary_series(conn, bucket="day", since_days=days),
        "hourly": summary_hourly(conn, since_days=days),
        "weekday": summary_weekday(conn, since_days=days),
        "usage": summary_usage(conn, limit=25, since_days=days),
        "top_commands": summary_top_commands(conn, limit=20, since_days=days),
        "heatmap": summary_heatmap(conn, days=days),
    }


def summary_full_report(conn, since_days: int = 30) -> dict[str, Any]:
    """Everything the report generator needs — same shape as full_report()."""
    return {
        "generated_at": time.time(),
        "since_days": since_days,
        "overview": summary_overview(conn, since_days=since_days),
        "series": summary_series(conn, bucket="day", since_days=since_days),
        "top_commands": summary_top_commands(conn, limit=20, since_days=since_days),
        "usage": summary_usage(conn, limit=25, since_days=since_days),
        "dirs": summary_dirs(conn, limit=10, since_days=since_days),
        "git": summary_git(conn, limit=10, since_days=since_days),
        "failures": summary_failures(conn, since_days=since_days),
        "durations": summary_durations(conn, since_days=since_days),
        "sessions": summary_sessions(conn, since_days=since_days),
        "alerts": summary_alert_summary(conn, since_days=since_days),
        "hourly": summary_hourly(conn, since_days=since_days),
        "weekday": summary_weekday(conn, since_days=since_days),
        "heatmap": summary_heatmap(conn, days=since_days),
        "entropy": summary_entropy(conn, since_days=since_days),
    }