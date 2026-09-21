"""Local-only web UI for Retrace.

Binds strictly to 127.0.0.1 — never 0.0.0.0. No external assets, no
CDN, no telemetry. The entire frontend is a single self-contained
HTML page served from memory. Data never leaves the machine.

Routes:
  GET  /                  → the UI (HTML)
  GET  /api/stats         → aggregate stats
  GET  /api/records       → recent records (?limit=, ?q=)
  GET  /api/alerts        → persisted alerts (?since=minutes)
  GET  /api/detect        → run detectors now, persist, return alerts
  POST /api/alerts/ack    → acknowledge alert {id}
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .db import connect, default_db_path

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Retrace — local timeline</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0f1115; --panel: #161a22; --panel2: #1c2130;
    --text: #e6e9ef; --muted: #8b93a7; --accent: #4f8cff;
    --crit: #ff5c5c; --high: #ff9f43; --med: #ffd166; --info: #4f8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; padding: 24px; }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  h1 { font-size: 18px; letter-spacing: 1px; }
  h1 .dot { color: var(--accent); }
  .badge { font-size: 11px; padding: 3px 8px; border-radius: 10px; background: #1f3a2d; color: #6fdc8c; }
  .badge.warn { background: #3a3320; color: #ffd166; }
  .stats { display: flex; gap: 24px; margin-bottom: 20px; flex-wrap: wrap; }
  .stat { background: var(--panel); border: 1px solid #232a3a; border-radius: 8px; padding: 10px 16px; min-width: 110px; }
  .stat .n { font-size: 22px; font-weight: 600; }
  .stat .l { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .row { display: flex; gap: 20px; flex-wrap: wrap; }
  .col { flex: 1 1 420px; background: var(--panel); border: 1px solid #232a3a; border-radius: 10px; padding: 14px; min-width: 320px; }
  h2 { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--muted); font-weight: 500; font-size: 11px; padding: 4px 8px; border-bottom: 1px solid #232a3a; }
  td { padding: 5px 8px; border-bottom: 1px solid #1a1f2b; vertical-align: top; }
  td.cmd { word-break: break-all; }
  .sev { font-size: 10px; padding: 2px 6px; border-radius: 8px; text-transform: uppercase; font-weight: 600; }
  .sev.critical { background: #3a1d1d; color: var(--crit); }
  .sev.high { background: #3a2a1a; color: var(--high); }
  .sev.medium { background: #3a3320; color: var(--med); }
  .sev.info { background: #1d2a3a; color: var(--info); }
  input[type=search] { background: var(--panel2); border: 1px solid #2a3245; color: var(--text); padding: 6px 10px; border-radius: 6px; width: 220px; font: inherit; }
  button { background: var(--panel2); border: 1px solid #2a3245; color: var(--text); padding: 6px 12px; border-radius: 6px; cursor: pointer; font: inherit; }
  button:hover { border-color: var(--accent); }
  .alert { display: flex; gap: 10px; padding: 8px 10px; border-radius: 8px; margin-bottom: 6px; background: var(--panel2); align-items: baseline; }
  .alert .msg { flex: 1; }
  .alert .ts { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .alert.acked { opacity: .45; }
  .muted { color: var(--muted); }
  .refresh { margin-left: auto; font-size: 11px; color: var(--muted); }
  .empty { color: var(--muted); padding: 12px; text-align: center; }
  footer { margin-top: 24px; color: var(--muted); font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1><span class="dot">●</span> RETRACE</h1>
  <span class="badge">LOCAL ONLY — 127.0.0.1</span>
  <span class="badge warn" id="lock">offline</span>
  <div class="refresh" id="refresh"></div>
</header>

<div class="stats" id="stats"></div>

<div class="row">
  <div class="col">
    <h2>Alerts</h2>
    <div id="alerts"><div class="empty">No alerts yet.</div></div>
    <p style="margin-top:8px"><button onclick="runDetect()">Run detectors now</button></p>
  </div>
  <div class="col">
    <h2>Timeline</h2>
    <p style="margin-bottom:8px"><input type="search" id="q" placeholder="Search commands…" oninput="debouncedLoad()"></p>
    <div style="max-height:520px; overflow:auto">
      <table>
        <thead><tr><th>Time</th><th>Shell</th><th>Command</th><th>Src</th></tr></thead>
        <tbody id="records"></tbody>
      </table>
    </div>
  </div>
</div>

<footer>Retrace — deterministic, offline terminal intelligence. No data leaves this machine.</footer>

<script>
const $ = (id) => document.getElementById(id);
let timer = null;
function debouncedLoad(){ clearTimeout(timer); timer = setTimeout(loadAll, 250); }

async function j(url, opts){
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

async function loadStats(){
  const s = await j("/api/stats");
  const by = Object.entries(s.by_source||{}).map(([k,v])=>k+":"+v).join(" · ");
  $("stats").innerHTML =
    `<div class="stat"><div class="n">${s.total}</div><div class="l">records</div></div>` +
    `<div class="stat"><div class="n">${s.alerts||0}</div><div class="l">alerts</div></div>` +
    `<div class="stat"><div class="n">${s.sources||0}</div><div class="l">sources</div></div>` +
    `<div class="stat"><div class="n">${s.last||"–"}</div><div class="l">last capture</div></div>` +
    `<div class="stat" style="min-width:220px"><div class="n" style="font-size:13px;padding-top:6px">${by||"–"}</div><div class="l">by source</div></div>`;
}

async function loadAlerts(){
  const a = await j("/api/alerts");
  const box = $("alerts");
  if (!a.length){ box.innerHTML = '<div class="empty">No alerts yet.</div>'; return; }
  box.innerHTML = a.map(al => `
    <div class="alert ${al.acked?"acked":""}" id="al-${al.id}">
      <span class="sev ${al.severity}">${al.severity}</span>
      <div class="msg"><b>${al.rule}</b> — ${esc(al.message)}${al.count>1?` <span class="muted">(×${al.count})</span>`:""}</div>
      <span class="ts">${fmt(al.last_ts)}</span>
      ${al.acked?"":`<button onclick="ack('${al.id}')">ack</button>`}
    </div>`).join("");
}

async function loadRecords(){
  const q = $("q").value.trim();
  const recs = await j("/api/records?limit=100&q="+encodeURIComponent(q));
  const body = $("records");
  if (!recs.length){ body.innerHTML = '<tr><td colspan="4" class="empty">No records.</td></tr>'; return; }
  body.innerHTML = recs.map(r => `
    <tr>
      <td style="white-space:nowrap">${fmt(r.ts)}</td>
      <td>${esc(r.shell||"")}</td>
      <td class="cmd">${esc(r.command||"")}</td>
      <td>${esc(r.source||"")}</td>
    </tr>`).join("");
}

async function runDetect(){
  const a = await j("/api/detect", {method:"POST"});
  $("alerts").innerHTML = `<div class="empty">${a.length} alert(s) fired.</div>`;
  loadAlerts();
}

async function ack(id){
  await j("/api/alerts/ack", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({id})});
  loadAlerts();
}

function fmt(ts){
  if (!ts) return "–";
  const d = new Date(ts*1000);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString();
}
function esc(s){
  return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function loadAll(){
  try {
    await Promise.all([loadStats(), loadAlerts(), loadRecords()]);
    $("refresh").textContent = "updated " + new Date().toLocaleTimeString();
  } catch(e){ $("refresh").textContent = "error: " + e.message; }
}
setInterval(loadAll, 15000);
loadAll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RetraceUI/0.1"

    def log_message(self, fmt, *args):  # keep stdout clean
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        conn = connect()

        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            conn.close()
            return

        try:
            if path == "/api/stats":
                s = stats_for_api(conn)
                self._json(s)
            elif path == "/api/records":
                limit = int(qs.get("limit", ["100"])[0])
                q = qs.get("q", [""])[0]
                self._json(records_for_api(conn, limit=limit, q=q))
            elif path == "/api/alerts":
                since_min = int(qs.get("since", ["1440"])[0])
                self._json(alerts_for_api(conn, since_minutes=since_min))
            elif path == "/api/detect":
                self._json(run_detect(conn))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # never leak internals to the client
            self._json({"error": type(exc).__name__}, 500)
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        conn = connect()
        try:
            if parsed.path == "/api/detect":
                self._json(run_detect(conn))
            elif parsed.path == "/api/alerts/ack":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                ack_alert(conn, body.get("id"))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": type(exc).__name__}, 500)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def stats_for_api(conn) -> dict:
    from .db import stats

    s = stats(conn)
    s["alerts"] = conn.execute("SELECT COUNT(*) FROM alerts WHERE acked=0").fetchone()[0]
    s["sources"] = len(s["by_source"])
    s["last"] = None
    if s["last_ts"]:
        s["last"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_ts"]))
    return s


def records_for_api(conn, limit: int = 100, q: str = "") -> list[dict]:
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT ts, shell, command, source FROM commands "
            "WHERE command LIKE ? OR cwd LIKE ? ORDER BY ts DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ts, shell, command, source FROM commands ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(zip(["ts", "shell", "command", "source"], r)) for r in rows]


def run_detect(conn) -> list[dict]:
    from .detectors import detect

    alerts = detect(conn, since_minutes=60, limit=5000)
    inserted = []
    for a in alerts:
        rid = insert_alert(conn, a)
        inserted.append({**a, "id": rid})
    return inserted


def alerts_for_api(conn, since_minutes: int = 1440) -> list[dict]:
    since = time.time() - since_minutes * 60
    rows = conn.execute(
        "SELECT id, ts, rule, severity, message, count, first_ts, last_ts, acked "
        "FROM alerts WHERE ts >= ? ORDER BY ts DESC LIMIT 200",
        (since,),
    ).fetchall()
    cols = ["id", "ts", "rule", "severity", "message", "count", "first_ts", "last_ts", "acked"]
    return [dict(zip(cols, r)) for r in rows]


def insert_alert(conn, alert: dict) -> str:
    import uuid

    rid = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO alerts (id, ts, rule, severity, message, count, first_ts, last_ts, samples, acked) "
        "VALUES (?,?,?,?,?,?,?,?,?,0)",
        (
            rid,
            time.time(),
            alert.get("rule"),
            alert.get("severity"),
            alert.get("message"),
            alert.get("count", 1),
            alert.get("first_ts"),
            alert.get("last_ts"),
            json.dumps(alert.get("samples", [])),
        ),
    )
    conn.commit()
    return rid


def ack_alert(conn, alert_id: str | None) -> None:
    if not alert_id:
        return
    conn.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
    conn.commit()


def serve(port: int = 8765) -> None:
    """Start the local-only web server. Blocks forever."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Retrace UI: http://127.0.0.1:{port}  (localhost only — no network exposure)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def remotes_for_api(conn) -> list[dict]:
    """Registered remote hosts + per-host record counts from the DB."""
    from . import remote

    out = []
    for rec in remote.load_hosts():
        name = rec.get("name", rec.get("host"))
        host = rec.get("host", "")
        user = rec.get("user")
        count = conn.execute(
            "SELECT COUNT(*) FROM commands WHERE source='remote' AND host=?",
            (host,),
        ).fetchone()[0]
        out.append({
            "name": name,
            "host": host,
            "user": user,
            "port": rec.get("port", 22),
            "records": count,
        })
    return out