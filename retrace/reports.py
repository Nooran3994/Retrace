"""Report generation for Retrace — HTML (printable to PDF), CSV, and JSON export.

Self-contained by design: the HTML report embeds all CSS inline so it renders
identically from disk or served over HTTP, and prints cleanly to PDF from any
browser (Ctrl+P → Save as PDF). No external assets, no CDN, no telemetry —
consistent with the local-only privacy contract.

The report reuses the analytics engine (retrace/analytics.py) so the numbers
in the dashboard and the numbers in a downloaded report always agree.

Public API:
    make_html_report(conn, since_days=30)         -> str  (full standalone HTML)
    make_csv(conn, since_days=30, limit=5000)     -> str  (CSV of records)
    make_json(conn, since_days=30)                -> str  (full_report() JSON)
"""

from __future__ import annotations

import csv
import html
import io
import json
import time
from typing import Any

from .analytics import full_report, range_query

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _fmt_dt(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _bars(pct: float, width: int = 100) -> str:
    """Inline-block horizontal bar for the HTML report."""
    pct = max(0.0, min(100.0, pct))
    fill = max(1, int(width * pct / 100))
    return (
        f'<span style="display:inline-block;width:{width}px;height:10px;'
        f'background:#1c2130;border:1px solid #2a3245;vertical-align:middle">'
        f'<span style="display:inline-block;width:{fill}px;height:10px;'
        f'background:#4f8cff;vertical-align:middle"></span></span>'
    )


def _stat_card(label: str, value: Any, sub: str = "") -> str:
    return (
        f'<div class="card"><div class="card-n">{_esc(value)}</div>'
        f'<div class="card-l">{_esc(label)}</div>'
        f"{f'<div class=\"card-s\">{_esc(sub)}</div>' if sub else ''}</div>"
    )


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def make_html_report(conn, since_days: int = 30) -> str:
    """Build a complete standalone HTML report document."""
    data = full_report(conn, since_days=since_days)
    o = data.get("overview", {})
    generated = _fmt_dt(data.get("generated_at"))

    # ---- stat cards -------------------------------------------------------
    cards = "".join(
        [
            _stat_card("Records", o.get("total", 0), f"last {since_days}d"),
            _stat_card("Hosts", o.get("hosts", 0)),
            _stat_card("Sessions", o.get("sessions", 0)),
            _stat_card("Alerts", o.get("alerts", 0)),
            _stat_card("Failed", o.get("failures", 0), f"{o.get('failure_rate', 0):.1f}%"),
            _stat_card("Sources", len(o.get("by_source", {}))),
        ]
    )

    # ---- by source / shell ------------------------------------------------
    src_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{v}</td></tr>"
        for k, v in sorted(o.get("by_source", {}).items(), key=lambda kv: -kv[1])
    )
    shell_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{v}</td></tr>"
        for k, v in sorted(o.get("by_shell", {}).items(), key=lambda kv: -kv[1])
    )

    # ---- time series -------------------------------------------------------
    series = data.get("series", [])
    if series:
        max_v = max(s.get("commands", 0) for s in series)
        ts_rows = "".join(
            f"<tr><td>{_esc(s.get('bucket'))}</td><td class='num'>{s.get('commands', 0)}</td>"
            f"<td>{_bars((s.get('commands', 0) / max_v * 100) if max_v else 0)}</td></tr>"
            for s in series
        )
    else:
        ts_rows = '<tr><td colspan="3" class="empty">No data in range.</td></tr>'

    # ---- top commands -----------------------------------------------------
    top = data.get("top_commands", [])
    if top:
        max_c = max(t.get("count", 0) for t in top)
        top_rows = "".join(
            f"<tr><td class='num'>{i + 1}</td><td>{_esc(t.get('command'))}</td>"
            f"<td class='num'>{t.get('count', 0)}</td>"
            f"<td>{_bars((t.get('count', 0) / max_c * 100) if max_c else 0)}</td></tr>"
            for i, t in enumerate(top)
        )
    else:
        top_rows = '<tr><td colspan="4" class="empty">No commands in range.</td></tr>'

    # ---- command usage (normalized) ---------------------------------------
    usage = data.get("usage", {})
    usage_items = usage.get("items", []) if isinstance(usage, dict) else usage
    if usage_items:
        max_u = max(u.get("count", 0) for u in usage_items)
        usage_rows = "".join(
            f"<tr><td>{_esc(u.get('name'))}</td><td class='num'>{u.get('count', 0)}</td>"
            f"<td class='num'>{u.get('share', 0):.1f}%</td>"
            f"<td>{_bars((u.get('count', 0) / max_u * 100) if max_u else 0)}</td></tr>"
            for u in usage_items
        )
    else:
        usage_rows = '<tr><td colspan="4" class="empty">No usage in range.</td></tr>'

    # ---- directories ------------------------------------------------------
    dirs = data.get("dirs", [])
    dir_rows = "".join(
        f"<tr><td>{_esc(d.get('cwd'))}</td><td class='num'>{d.get('count', 0)}</td></tr>"
        for d in dirs
    ) or '<tr><td colspan="2" class="empty">No directories recorded.</td></tr>'

    # ---- git --------------------------------------------------------------
    git = data.get("git", {})
    git_repos = git.get("repos", []) if isinstance(git, dict) else []
    git_rows = "".join(
        f"<tr><td>{_esc(g.get('repo'))}</td><td class='num'>{g.get('count', 0)}</td>"
        f"<td>{_esc(g.get('branch', ''))}</td><td>{'yes' if g.get('dirty') else 'no'}</td></tr>"
        for g in git_repos
    ) or '<tr><td colspan="4" class="empty">No git activity in range.</td></tr>'

    # ---- failures ---------------------------------------------------------
    fail = data.get("failures", {})
    fail_by_code = fail.get("by_code", []) if isinstance(fail, dict) else []
    fail_rows = "".join(
        f"<tr><td>{_esc(item.get('exit_code'))}</td><td class='num'>{item.get('count', 0)}</td></tr>"
        for item in sorted(fail_by_code, key=lambda kv: -kv.get("count", 0))
    ) or '<tr><td colspan="2" class="empty">No failures in range.</td></tr>'

    # ---- durations --------------------------------------------------------
    dur = data.get("durations", {})
    dur_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{_esc(v)}</td></tr>"
        for k, v in dur.items()
        if k in ("p50_ms", "p95_ms", "p99_ms") and v is not None
    )

    # ---- sessions ---------------------------------------------------------
    sess = data.get("sessions", {})
    if isinstance(sess, dict) and sess:
        sess_rows = "".join(
            f"<tr><td>{_esc(k)}</td><td class='num'>{_esc(v)}</td></tr>"
            for k, v in sess.items()
        )
    else:
        sess_rows = '<tr><td colspan="2" class="empty">No session data.</td></tr>'

    # ---- alerts -----------------------------------------------------------
    alerts = data.get("alerts", {})
    by_rule = alerts.get("by_rule", {}) if isinstance(alerts, dict) else {}
    by_sev = alerts.get("by_severity", {}) if isinstance(alerts, dict) else {}
    alert_rows = "".join(
        f"<tr><td>{_esc(item.get('rule'))}</td><td class='num'>{item.get('count', 0)}</td></tr>"
        for item in sorted(by_rule, key=lambda kv: -kv.get("count", 0))
    ) or '<tr><td colspan="2" class="empty">No alerts in range.</td></tr>'

    # ---- hourly / weekday -------------------------------------------------
    hourly = data.get("hourly", [])
    if hourly:
        max_h = max(h.get("count", 0) for h in hourly)
        hour_rows = "".join(
            f"<tr><td>{h.get('hour'):02d}:00</td><td class='num'>{h.get('count', 0)}</td>"
            f"<td>{_bars((h.get('count', 0) / max_h * 100) if max_h else 0)}</td></tr>"
            for h in hourly
        )
    else:
        hour_rows = '<tr><td colspan="3" class="empty">No data.</td></tr>'

    week = data.get("weekday", [])
    if week:
        max_w = max(w.get("count", 0) for w in week)
        week_rows = "".join(
            f"<tr><td>{_esc(w.get('day'))}</td><td class='num'>{w.get('count', 0)}</td>"
            f"<td>{_bars((w.get('count', 0) / max_w * 100) if max_w else 0)}</td></tr>"
            for w in week
        )
    else:
        week_rows = '<tr><td colspan="3" class="empty">No data.</td></tr>'

    # ---- entropy (high-entropy commands) ----------------------------------
    entropy = data.get("entropy", {})
    ent_recent = entropy.get("recent", []) if isinstance(entropy, dict) else []
    ent_rows = "".join(
        f"<tr><td>{_fmt_ts(e.get('ts'))}</td><td class='cmd'>{_esc(e.get('command'))}</td></tr>"
        for e in ent_recent[:20]
    ) or '<tr><td colspan="2" class="empty">No high-entropy commands in range.</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Retrace Report — last {since_days} days</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font: 13px/1.5 -apple-system, 'Segoe UI', Roboto, sans-serif; color: #222; margin: 32px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: #666; font-size: 12px; margin-bottom: 24px; }}
  h2 {{ font-size: 15px; margin: 24px 0 8px; border-bottom: 2px solid #4f8cff; padding-bottom: 4px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .card {{ background: #f4f6fb; border: 1px solid #d8dee9; border-radius: 8px; padding: 10px 16px; min-width: 120px; }}
  .card-n {{ font-size: 22px; font-weight: 600; }}
  .card-l {{ color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }}
  .card-s {{ color: #999; font-size: 11px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0 16px; }}
  th {{ text-align: left; color: #666; font-weight: 600; font-size: 11px; padding: 5px 8px; border-bottom: 1px solid #c8d0dc; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #e2e6ee; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.cmd {{ font-family: ui-monospace, Consolas, monospace; word-break: break-all; }}
  .empty {{ color: #999; text-align: center; padding: 10px; }}
  .two {{ display: flex; gap: 24px; }}
  .two > div {{ flex: 1 1 50%; }}
  footer {{ margin-top: 32px; color: #999; font-size: 11px; }}
  @media print {{ body {{ margin: 12px; }} .card {{ border: 1px solid #bbb; }} }}
</style>
</head>
<body>
<h1>Retrace Report</h1>
<div class="meta">Generated {generated} · window: last {since_days} days · local-only · no data leaves this machine</div>

<h2>Overview</h2>
<div class="cards">{cards}</div>

<div class="two">
  <div>
    <h2>By Source</h2>
    <table><thead><tr><th>Source</th><th class="num">Records</th></tr></thead><tbody>{src_rows}</tbody></table>
  </div>
  <div>
    <h2>By Shell</h2>
    <table><thead><tr><th>Shell</th><th class="num">Records</th></tr></thead><tbody>{shell_rows}</tbody></table>
  </div>
</div>

<h2>Daily Activity</h2>
<table><thead><tr><th>Day</th><th class="num">Commands</th><th>Trend</th></tr></thead><tbody>{ts_rows}</tbody></table>

<div class="two">
  <div>
    <h2>Top Commands</h2>
    <table><thead><tr><th>#</th><th>Command</th><th class="num">Count</th><th>Share</th></tr></thead><tbody>{top_rows}</tbody></table>
  </div>
  <div>
    <h2>Command Usage</h2>
    <table><thead><tr><th>Command</th><th class="num">Count</th><th class="num">Share</th><th>Share</th></tr></thead><tbody>{usage_rows}</tbody></table>
  </div>
</div>

<div class="two">
  <div>
    <h2>Working Directories</h2>
    <table><thead><tr><th>Directory</th><th class="num">Count</th></tr></thead><tbody>{dir_rows}</tbody></table>
  </div>
  <div>
    <h2>Git Activity</h2>
    <table><thead><tr><th>Repo</th><th class="num">Commands</th><th>Branch</th><th>Dirty</th></tr></thead><tbody>{git_rows}</tbody></table>
  </div>
</div>

<div class="two">
  <div>
    <h2>Failures by Exit Code</h2>
    <table><thead><tr><th>Exit</th><th class="num">Count</th></tr></thead><tbody>{fail_rows}</tbody></table>
  </div>
  <div>
    <h2>Duration Percentiles</h2>
    <table><thead><tr><th>Metric</th><th class="num">Value</th></tr></thead><tbody>{dur_rows}</tbody></table>
  </div>
</div>

<div class="two">
  <div>
    <h2>Session Stats</h2>
    <table><thead><tr><th>Metric</th><th class="num">Value</th></tr></thead><tbody>{sess_rows}</tbody></table>
  </div>
  <div>
    <h2>Alerts</h2>
    <table><thead><tr><th>Rule</th><th class="num">Count</th><th>Severity</th></tr></thead><tbody>{alert_rows}</tbody></table>
  </div>
</div>

<div class="two">
  <div>
    <h2>Hourly Profile</h2>
    <table><thead><tr><th>Hour</th><th class="num">Commands</th><th>Activity</th></tr></thead><tbody>{hour_rows}</tbody></table>
  </div>
  <div>
    <h2>Weekday Profile</h2>
    <table><thead><tr><th>Day</th><th class="num">Commands</th><th>Activity</th></tr></thead><tbody>{week_rows}</tbody></table>
  </div>
</div>

<h2>High-Entropy Commands (possible secrets)</h2>
<table><thead><tr><th>Time</th><th>Command</th><th class="num">Entropy</th></tr></thead><tbody>{ent_rows}</tbody></table>

<footer>Generated by Retrace — deterministic, offline terminal intelligence. Report window: last {since_days} days.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CSV / JSON export
# ---------------------------------------------------------------------------


def make_csv(conn, since_days: int = 30, limit: int = 5000) -> str:
    """CSV export of filtered records. Deterministic ordering (ts DESC)."""
    rows = range_query(conn, since_days=since_days, limit=limit)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["timestamp", "source", "shell", "command", "cwd", "exit_code", "duration_ms", "host", "git_repo", "git_branch"]
    )
    for r in rows:
        writer.writerow(
            [
                _fmt_dt(r.get("ts")),
                r.get("source", ""),
                r.get("shell", ""),
                r.get("command", ""),
                r.get("cwd", ""),
                r.get("exit_code", ""),
                r.get("duration_ms", ""),
                r.get("host", ""),
                r.get("git_repo", ""),
                r.get("git_branch", ""),
            ]
        )
    return out.getvalue()


def make_json(conn, since_days: int = 30) -> str:
    """Full analytics payload as pretty JSON."""
    return json.dumps(full_report(conn, since_days=since_days), indent=2, default=str)