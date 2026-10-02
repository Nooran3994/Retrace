"""Local-only web UI for Retrace.

Binds strictly to 127.0.0.1 — never 0.0.0.0. No external assets, no
CDN, no telemetry. The entire frontend is a single self-contained
HTML page served from memory. Data never leaves the machine.

The UI is a tabbed single-page app:
  Dashboard  — stat cards + dependency-free SVG charts (activity
               heatmap, hourly/weekday profiles, command usage,
               top commands, daily time series)
  Timeline   — searchable command history
  Alerts     — persisted alerts, run detectors, acknowledge
  Remotes    — registered remote hosts (Phase 4 adds management)

Routes:
  GET  /                          → the UI (HTML)
  GET  /api/stats                 → aggregate stats (legacy)
  GET  /api/analytics/dashboard   → everything the dashboard charts need
  GET  /api/records               → recent records (?limit=, ?q=)
  GET  /api/alerts                → persisted alerts (?since=minutes)
  GET  /api/remotes               → registered remote hosts
  GET  /api/detect                → run detectors now, persist, return alerts
  POST /api/alerts/ack            → acknowledge alert {id}
  GET  /api/report                → report export (?format=html|csv|json&days=)
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_dashboard_cache = {"ts": 0.0, "days": 0, "data": None}
_DASH_TTL = 60.0
from urllib.parse import urlparse, parse_qs

from .db import connect, default_db_path, retain, db_stats, vacuum

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Retrace — local intelligence</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0f1115; --panel: #161a22; --panel2: #1c2130;
    --text: #e6e9ef; --muted: #8b93a7; --accent: #4f8cff;
    --crit: #ff5c5c; --high: #ff9f43; --med: #ffd166; --info: #4f8cff;
    --ok: #6fdc8c; --border: #232a3a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; padding: 20px 24px; }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  h1 { font-size: 18px; letter-spacing: 1px; }
  h1 .dot { color: var(--accent); }
  .badge { font-size: 11px; padding: 3px 8px; border-radius: 10px; background: #1f3a2d; color: var(--ok); }
  .badge.warn { background: #3a3320; color: var(--med); }
  .refresh { margin-left: auto; font-size: 11px; color: var(--muted); }

  nav { display: flex; gap: 4px; margin-bottom: 18px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .tab { background: none; border: none; color: var(--muted); padding: 8px 16px; font: inherit; font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .tabpane { display: none; }
  .tabpane.active { display: block; }

  .stats { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 108px; flex: 1 1 108px; }
  .stat .n { font-size: 22px; font-weight: 600; }
  .stat .l { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .5px; }
  .stat .sub { color: var(--muted); font-size: 11px; margin-top: 2px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; margin-bottom: 14px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; min-width: 0; }
  .card.wide { grid-column: 1 / -1; }
  h2 { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
  h2 .hint { float: right; font-weight: 400; text-transform: none; letter-spacing: 0; }
  svg { display: block; width: 100%; height: auto; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
  .chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--panel2); color: var(--muted); border: 1px solid var(--border); }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--muted); font-weight: 500; font-size: 11px; padding: 4px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 5px 8px; border-bottom: 1px solid #1a1f2b; vertical-align: top; }
  td.cmd { word-break: break-all; }
  .sev { font-size: 10px; padding: 2px 6px; border-radius: 8px; text-transform: uppercase; font-weight: 600; }
  .sev.critical { background: #3a1d1d; color: var(--crit); }
  .sev.high { background: #3a2a1a; color: var(--high); }
  .sev.medium { background: #3a3320; color: var(--med); }
  .sev.info { background: #1d2a3a; color: var(--info); }
  input[type=search], select { background: var(--panel2); border: 1px solid #2a3245; color: var(--text); padding: 6px 10px; border-radius: 6px; font: inherit; }
  input[type=search] { width: 220px; }
  button { background: var(--panel2); border: 1px solid #2a3245; color: var(--text); padding: 6px 12px; border-radius: 6px; cursor: pointer; font: inherit; }
  button:hover { border-color: var(--accent); }
  .alert { display: flex; gap: 10px; padding: 8px 10px; border-radius: 8px; margin-bottom: 6px; background: var(--panel2); align-items: baseline; }
  .alert .msg { flex: 1; }
  .alert .ts { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .alert.acked { opacity: .45; }
  .muted { color: var(--muted); }
  .empty { color: var(--muted); padding: 16px; text-align: center; }
  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
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

<nav>
  <button class="tab active" data-tab="dashboard" onclick="switchTab('dashboard')">Dashboard</button>
  <button class="tab" data-tab="timeline" onclick="switchTab('timeline')">Timeline</button>
  <button class="tab" data-tab="alerts" onclick="switchTab('alerts')">Alerts</button>
  <button class="tab" data-tab="remotes" onclick="switchTab('remotes')">Remotes</button>
  <button class="tab" data-tab="maint" onclick="switchTab('maint')">Maintenance</button>
  <button class="tab" data-tab="reports" onclick="switchTab('reports')">Reports</button>
  <button class="tab" data-tab="settings" onclick="switchTab('settings')">Settings</button>
</nav>

<!-- ============ DASHBOARD ============ -->
<div class="tabpane active" id="tab-dashboard">
  <div class="toolbar">
    <span class="muted">Period</span>
    <select id="period" onchange="loadDashboard()">
      <option value="7">7 days</option>
      <option value="30" selected>30 days</option>
      <option value="90">90 days</option>
    </select>
  </div>
  <div class="stats" id="d-stats"></div>
  <div class="grid">
    <div class="card wide"><h2>Activity heatmap <span class="hint">hour of day × day</span></h2><div id="c-heat"></div></div>
    <div class="card"><h2>Commands per hour</h2><div id="c-hourly"></div></div>
    <div class="card"><h2>Commands per weekday</h2><div id="c-weekday"></div></div>
    <div class="card"><h2>Daily series</h2><div id="c-series"></div></div>
    <div class="card"><h2>Command usage <span class="hint">share of activity</span></h2><div id="c-usage"></div></div>
    <div class="card wide"><h2>Top commands <span class="hint">exact text</span></h2><div id="c-top"></div></div>
  </div>
</div>

<!-- ============ TIMELINE ============ -->
<div class="tabpane" id="tab-timeline">
  <div class="toolbar">
    <input type="search" id="q" placeholder="Search commands…" oninput="debouncedLoad()">
    <span class="muted" id="rec-count"></span>
  </div>
  <div class="card">
    <div style="max-height:560px; overflow:auto">
      <table>
        <thead><tr><th>Time</th><th>Shell</th><th>Command</th><th>Src</th></tr></thead>
        <tbody id="records"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ============ ALERTS ============ -->
<div class="tabpane" id="tab-alerts">
  <div class="toolbar">
    <button onclick="runDetect()">Run detectors now</button>
    <span class="muted">Detectors scan recent commands for risky patterns (secrets, destructive ops, sudo).</span>
  </div>
  <div id="alerts"><div class="empty">No alerts yet.</div></div>
</div>

<!-- ============ REMOTES ============ -->
<div class="tabpane" id="tab-remotes">
  <div id="remotes"><div class="empty">Loading…</div></div>
</div>

<!-- ============ MAINTENANCE ============ -->
<div class="tabpane" id="tab-maint">
  <div class="toolbar">
    <button onclick="loadMaint()">Refresh stats</button>
    <span class="muted" id="maint-msg"></span>
  </div>
  <div class="stats" id="maint-stats"></div>
  <div class="grid">
    <div class="card">
      <h2>Retention <span class="hint">prune old records</span></h2>
      <div class="toolbar">
        <select id="retain-days">
          <option value="30">30 days</option>
          <option value="60" selected>60 days</option>
          <option value="90">90 days</option>
          <option value="180">180 days</option>
        </select>
        <button onclick="doRetain()">Delete older than…</button>
      </div>
      <p class="muted" style="margin-top:8px;font-size:12px">Deletes command and alert rows older than the chosen window. Keeps the newest data, frees space on next vacuum.</p>
    </div>
    <div class="card">
      <h2>Vacuum <span class="hint">reclaim disk space</span></h2>
      <div class="toolbar">
        <button onclick="doVacuum()">Run VACUUM</button>
      </div>
      <p class="muted" style="margin-top:8px;font-size:12px">Rebuilds the database file, reclaiming space freed by retention. Safe to run anytime; takes a few seconds on large DBs.</p>
    </div>
  </div>
</div>


<!-- ============ REPORTS ============ -->
<div class="tabpane" id="tab-reports">
  <div class="toolbar">
    <span class="muted">Period</span>
    <select id="report-days">
      <option value="7">7 days</option>
      <option value="30" selected>30 days</option>
      <option value="90">90 days</option>
    </select>
    <span class="muted">Format</span>
    <select id="report-format">
      <option value="html" selected>HTML (printable to PDF)</option>
      <option value="csv">CSV (records)</option>
      <option value="json">JSON (full payload)</option>
    </select>
    <button onclick="downloadReport()">⬇ Download report</button>
    <span class="muted" id="report-msg"></span>
  </div>
  <div class="card">
    <h2>Report preview <span class="hint">opens in a new tab — Ctrl+P to save as PDF</span></h2>
    <div class="toolbar">
      <button onclick="previewReport()">Preview in new tab</button>
    </div>
    <p class="muted" style="margin-top:8px;font-size:12px">The HTML report is fully self-contained (inline CSS + inline SVG) — it renders offline and prints cleanly to PDF. CSV gives the raw filtered records; JSON gives the complete analytics payload for scripting.</p>
  </div>
</div>

<!-- ============ SETTINGS ============ -->
<div class="tabpane" id="tab-settings">
  <div class="toolbar">
    <button onclick="loadSettings()">Refresh</button>
    <span class="muted" id="settings-msg"></span>
  </div>
  <div class="grid">
    <div class="card">
      <h2>Model provider <span class="hint">optional local analysis</span></h2>
      <div style="display:grid;grid-template-columns:110px 1fr;gap:6px;align-items:center">
        <span class="muted">Provider</span>
        <select id="cfg-provider">
          <option value="null">null (off)</option>
          <option value="ollama">ollama (local)</option>
          <option value="openai">openai-compatible</option>
        </select>
        <span class="muted">Model</span>
        <input type="text" id="cfg-model" placeholder="llama3.2:3b">
        <span class="muted">URL</span>
        <input type="text" id="cfg-url" placeholder="http://127.0.0.1:11434">
        <span class="muted">Timeout (s)</span>
        <input type="number" id="cfg-timeout" value="60">
        <span class="muted">Max chars</span>
        <input type="number" id="cfg-maxchars" value="12000">
      </div>
      <div class="toolbar" style="margin-top:8px">
        <label><input type="checkbox" id="cfg-enabled"> Enable model analysis</label>
        <button onclick="saveConfig()">Save config</button>
      </div>
      <p class="muted" style="margin-top:6px;font-size:11px">Only redacted text is ever sent to a model. Capture + detect never depend on it.</p>
    </div>
    <div class="card">
      <h2>Control plane <span class="hint">CLI actions from the UI</span></h2>
      <div class="toolbar">
        <button onclick="runAction('ingest','history')">Ingest history</button>
        <button onclick="runAction('ingest','flight')">Ingest flight</button>
        <button onclick="runAction('ingest','ps')">Ingest PS</button>
        <button onclick="runAction('detect')">Run detectors</button>
        <button onclick="runAction('agent-cycle')">Agent cycle</button>
        <button onclick="runAction('win-events')">Win events</button>
        <button onclick="runAction('collect','all')">Collect remotes</button>
      </div>
      <p class="muted" style="margin-top:8px;font-size:12px">Long actions run in the background — the message line shows the job id, then the result appears below.</p>
      <div id="job-result" class="muted" style="margin-top:6px;word-break:break-all"></div>
    </div>
  </div>
  <div class="card wide">
    <h2>Detectors <span class="hint">rule-based alerting — offline, deterministic</span></h2>
    <div class="toolbar">
      <span class="muted">New rule</span>
      <input type="text" id="det-name" style="width:130px" placeholder="my-rule">
      <select id="det-sev">
        <option>info</option><option>medium</option><option>high</option><option selected>critical</option>
      </select>
      <input type="text" id="det-match" style="width:220px" placeholder="\bpattern\b">
      <button onclick="addDetector()">+ Add rule</button>
    </div>
    <div class="toolbar">
      <span class="muted">Test regex</span>
      <input type="text" id="det-sample" style="width:300px" placeholder="sample command to test against">
      <button onclick="testDetector()">Test</button>
      <span class="muted" id="det-test-result"></span>
    </div>
    <div style="max-height:420px; overflow:auto">
      <table>
        <thead><tr><th>Rule</th><th>Severity</th><th>Match</th><th>Window</th><th>Thresh</th><th></th></tr></thead>
        <tbody id="det-rules"></tbody>
      </table>
    </div>
  </div>
  <div class="card wide">
    <h2>Remote hosts <span class="hint">agentless SSH collection</span></h2>
    <div class="toolbar">
      <input type="text" id="rm-name" style="width:110px" placeholder="name">
      <input type="text" id="rm-host" style="width:180px" placeholder="host or IP">
      <input type="text" id="rm-user" style="width:90px" placeholder="user">
      <input type="number" id="rm-port" style="width:60px" value="22">
      <button onclick="addRemote()">+ Add remote</button>
    </div>
    <table>
      <thead><tr><th>Name</th><th>Host</th><th>User</th><th>Port</th><th></th></tr></thead>
      <tbody id="rm-list"></tbody>
    </table>
  </div>
</div>

<footer>Retrace — deterministic, offline terminal intelligence. No data leaves this machine.</footer>

<script>
const $ = (id) => document.getElementById(id);
let timer = null;
function debouncedLoad(){ clearTimeout(timer); timer = setTimeout(loadRecords, 250); }

async function j(url, opts){
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

/* ---------- tiny SVG chart library (dependency-free) ---------- */
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function barChart(el, data, opts){
  opts = opts || {};
  el.innerHTML = "";
  const W = el.clientWidth || 600, H = opts.height || 150;
  const pad = {t: 8, r: 4, b: 20, l: 30};
  const max = Math.max(1, ...data.map(d => d.value));
  const svg = svgEl("svg", {width: W, height: H, viewBox: "0 0 " + W + " " + H});
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  for (let i = 0; i <= 4; i++){
    const y = pad.t + ih - ih * i / 4;
    svg.appendChild(svgEl("line", {x1: pad.l, y1: y, x2: W - pad.r, y2: y, stroke: "#232a3a"}));
    const t = svgEl("text", {x: pad.l - 4, y: y + 3, "text-anchor": "end", fill: "#8b93a7", "font-size": 9});
    t.textContent = Math.round(max * i / 4);
    svg.appendChild(t);
  }
  const bw = iw / Math.max(1, data.length);
  data.forEach((d, i) => {
    const h = d.value / max * ih;
    const x = pad.l + i * bw + bw * 0.15, w = bw * 0.7;
    const rect = svgEl("rect", {x: x, y: pad.t + ih - h, width: w, height: Math.max(0, h), fill: opts.color || "#4f8cff", rx: 2});
    const title = svgEl("title", {});
    title.textContent = (opts.tooltip ? opts.tooltip(d) : d.label + ": " + d.value);
    rect.appendChild(title);
    svg.appendChild(rect);
    if (data.length <= 16){
      const t = svgEl("text", {x: x + w / 2, y: H - 6, "text-anchor": "middle", fill: "#8b93a7", "font-size": 9});
      t.textContent = d.label;
      svg.appendChild(t);
    }
  });
  el.appendChild(svg);
}
function hbarChart(el, data, opts){
  opts = opts || {};
  el.innerHTML = "";
  const W = el.clientWidth || 520;
  const rowH = 22;
  const H = Math.max(40, data.length * rowH + 4);
  const svg = svgEl("svg", {width: W, height: H, viewBox: "0 0 " + W + " " + H});
  const maxV = Math.max(1, ...data.map(d => d.value));
  const lblW = Math.min(170, W * 0.38);
  const barW = W - lblW - 46;
  data.forEach((d, i) => {
    const y = 4 + i * rowH;
    const t = svgEl("text", {x: lblW - 6, y: y + 11, "text-anchor": "end", fill: "#e6e9ef", "font-size": 11});
    t.textContent = d.label;
    svg.appendChild(t);
    const w = Math.max(0, d.value / maxV * barW);
    const rect = svgEl("rect", {x: lblW, y: y + 2, width: w, height: 14, fill: opts.color || "#4f8cff", rx: 3});
    const title = svgEl("title", {});
    title.textContent = (d.sub || d.label) + ": " + d.value;
    rect.appendChild(title);
    svg.appendChild(rect);
    const v = svgEl("text", {x: lblW + w + 4, y: y + 13, fill: "#8b93a7", "font-size": 10});
    v.textContent = opts.format ? opts.format(d.value) : d.value;
    svg.appendChild(v);
  });
  el.appendChild(svg);
}
function lineChart(el, labels, seriesList, opts){
  opts = opts || {};
  el.innerHTML = "";
  const W = el.clientWidth || 600, H = opts.height || 160;
  const pad = {t: 10, r: 8, b: 20, l: 34};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const max = Math.max(1, ...seriesList.map(s => Math.max(...s.values)));
  const svg = svgEl("svg", {width: W, height: H, viewBox: "0 0 " + W + " " + H});
  for (let i = 0; i <= 4; i++){
    const y = pad.t + ih - ih * i / 4;
    svg.appendChild(svgEl("line", {x1: pad.l, y1: y, x2: W - pad.r, y2: y, stroke: "#232a3a"}));
    const t = svgEl("text", {x: pad.l - 4, y: y + 3, "text-anchor": "end", fill: "#8b93a7", "font-size": 9});
    t.textContent = Math.round(max * i / 4);
    svg.appendChild(t);
  }
  const step = iw / Math.max(1, labels.length - 1);
  seriesList.forEach(s => {
    let path = "", area = "";
    s.values.forEach((v, i) => {
      const x = pad.l + i * step, y = pad.t + ih - (v / max * ih);
      path += (i ? "L" : "M") + x + "," + y;
      area += (i ? "L" : "M") + x + "," + y;
    });
    svg.appendChild(svgEl("path", {d: path, fill: "none", stroke: s.color, "stroke-width": 2}));
    if (s.area){
      area += "L" + (pad.l + (s.values.length - 1) * step) + "," + (pad.t + ih) + "L" + pad.l + "," + (pad.t + ih) + "Z";
      svg.appendChild(svgEl("path", {d: area, fill: s.color, opacity: 0.12}));
    }
  });
  const every = Math.ceil(labels.length / 6);
  labels.forEach((lb, i) => {
    if (i % every === 0 || i === labels.length - 1){
      const t = svgEl("text", {x: pad.l + i * step, y: H - 6, "text-anchor": "middle", fill: "#8b93a7", "font-size": 9});
      t.textContent = lb;
      svg.appendChild(t);
    }
  });
  el.appendChild(svg);
}
function heatmap(el, cells, days){
  el.innerHTML = "";
  const W = el.clientWidth || 720;
  const cell = 14, gap = 3, colW = cell + gap;
  const H = 24 * colW + 18;
  const svg = svgEl("svg", {width: W, height: H, viewBox: "0 0 " + W + " " + H});
  const max = Math.max(1, ...cells.map(c => c.count));
  const byKey = new Map(cells.map(c => [c.date + "|" + c.hour, c.count]));
  const dates = [];
  for (let i = days - 1; i >= 0; i--){
    const d = new Date(Date.now() - i * 86400000);
    dates.push(d.toISOString().slice(0, 10));
  }
  const startX = 34;
  dates.forEach((date, di) => {
    const x = startX + di * colW;
    if (di === 0){
      for (let h = 0; h < 24; h++){
        const t = svgEl("text", {x: startX - 4, y: 2 + h * colW + 10, "text-anchor": "end", fill: "#8b93a7", "font-size": 8});
        t.textContent = h;
        svg.appendChild(t);
      }
    }
    for (let h = 0; h < 24; h++){
      const n = byKey.get(date + "|" + h) || 0;
      const alpha = n ? 0.25 + 0.75 * (n / max) : 0.06;
      const rect = svgEl("rect", {x: x, y: 2 + h * colW, width: cell, height: cell, rx: 2, fill: n ? "rgba(79,140,255," + alpha.toFixed(2) + ")" : "#1a1f2b"});
      const title = svgEl("title", {});
      title.textContent = date + " " + h + ":00 — " + n + " commands";
      rect.appendChild(title);
      svg.appendChild(rect);
    }
  });
  dates.forEach((date, di) => {
    if (di % 7 === 0){
      const t = svgEl("text", {x: startX + di * colW + cell / 2, y: H - 4, "text-anchor": "middle", fill: "#8b93a7", "font-size": 8});
      t.textContent = date.slice(5);
      svg.appendChild(t);
    }
  });
  el.appendChild(svg);
}

/* ---------- data loaders ---------- */
function statCards(o){
  const cards = [
    ["records", o.total, o.hosts + " host" + (o.hosts === 1 ? "" : "s") + " · " + o.sessions + " sessions"],
    ["alerts open", o.alerts_open, o.alerts_total + " total"],
    ["failures", o.failures, (o.failure_rate || 0) + "% of commands with exit codes"],
    ["sources", Object.keys(o.by_source || {}).length, (o.by_shell || {}).length + " shells"]
  ];
  let html = cards.map(c =>
    '<div class="stat"><div class="n">' + c[1] + '</div><div class="l">' + c[0] + '</div>' +
    (c[2] ? '<div class="sub">' + c[2] + '</div>' : '') + '</div>'
  ).join("");
  const chips = Object.entries(o.by_source || {}).map(([k, v]) =>
    '<span class="chip">' + esc(k) + " · " + v + "</span>").join("");
  html += '<div class="stat" style="flex:2 1 240px"><div class="l" style="margin-bottom:4px">by source</div><div class="chips">' + (chips || '<span class="muted">no data</span>') + '</div></div>';
  return html;
}

async function loadDashboard(){
  try {
    const days = parseInt($("period").value, 10);
    $("d-stats").innerHTML = '<div class="empty">Loading…</div>';
    const d = await j("/api/analytics/dashboard?days=" + days);
    $("d-stats").innerHTML = statCards(d.overview);
    heatmap($("c-heat"), d.heatmap.cells, d.heatmap.days);
    barChart($("c-hourly"), d.hourly.map(h => ({label: String(h.hour), value: h.count})),
      {color: "#4f8cff", tooltip: h => h.label + ":00 — " + h.value + " commands"});
    barChart($("c-weekday"), d.weekday.map(w => ({label: w.day, value: w.count})), {color: "#6fdc8c"});
    lineChart($("c-series"), d.series.map(s => s.bucket.slice(5)),
      [{name: "commands", values: d.series.map(s => s.commands), color: "#4f8cff", area: true}], {height: 150});
    hbarChart($("c-usage"), d.usage.items.slice(0, 12).map(u => ({label: u.name, value: u.count, sub: u.name})),
      {color: "#ffd166", format: v => v});
    hbarChart($("c-top"), d.top_commands.slice(0, 10).map(t => ({
      label: t.command.length > 30 ? t.command.slice(0, 29) + "…" : t.command,
      value: t.count, sub: t.command
    })), {color: "#4f8cff"});
    bump("dashboard");
  } catch (e) { $("refresh").textContent = "error: " + e.message; }
}

async function loadRecords(){
  try {
    const q = $("q").value.trim();
    const recs = await j("/api/records?limit=100&q=" + encodeURIComponent(q));
    const body = $("records");
    $("rec-count").textContent = recs.length ? recs.length + " shown" : "";
    if (!recs.length){ body.innerHTML = '<tr><td colspan="4" class="empty">No records.</td></tr>'; return; }
    body.innerHTML = recs.map(r =>
      "<tr><td style='white-space:nowrap'>" + fmt(r.ts) + "</td>" +
      "<td>" + esc(r.shell || "") + "</td>" +
      "<td class='cmd'>" + esc(r.command || "") + "</td>" +
      "<td>" + esc(r.source || "") + "</td></tr>").join("");
    bump("timeline");
  } catch (e) { $("refresh").textContent = "error: " + e.message; }
}

async function loadAlerts(){
  try {
    const a = await j("/api/alerts");
    const box = $("alerts");
    if (!a.length){ box.innerHTML = '<div class="empty">No alerts yet.</div>'; bump("alerts"); return; }
    box.innerHTML = a.map(al =>
      '<div class="alert ' + (al.acked ? "acked" : "") + '" id="al-' + al.id + '">' +
      '<span class="sev ' + al.severity + '">' + al.severity + "</span>" +
      "<div class='msg'><b>" + esc(al.rule) + "</b> — " + esc(al.message) +
      (al.count > 1 ? " <span class='muted'>(×" + al.count + ")</span>" : "") + "</div>" +
      "<span class='ts'>" + fmt(al.last_ts) + "</span>" +
      (al.acked ? "" : '<button data-ack="' + al.id + '">ack</button>') +
      "</div>").join("");
    bump("alerts");
  } catch (e) { $("refresh").textContent = "error: " + e.message; }
}

async function loadRemotes(){
  try {
    const r = await j("/api/remotes");
    const box = $("remotes");
    if (!r.length){
      box.innerHTML = '<div class="card"><div class="empty">No remotes configured yet.<br><span class="muted">Remote host management arrives in Phase 4 (Settings UI).</span></div></div>';
      bump("remotes"); return;
    }
    box.innerHTML = '<div class="card"><table><thead><tr><th>Name</th><th>Host</th><th>User</th><th>Port</th><th>Records</th></tr></thead><tbody>' +
      r.map(x => "<tr><td>" + esc(x.name || "—") + "</td><td>" + esc(x.host) + "</td><td>" + esc(x.user || "—") + "</td><td>" + x.port + "</td><td>" + x.records + "</td></tr>").join("") +
      "</tbody></table></div>";
    bump("remotes");
  } catch (e) { $("refresh").textContent = "error: " + e.message; }
}

async function runDetect(){
  const a = await j("/api/detect", {method: "POST"});
  loadAlerts();
  $("refresh").textContent = "detectors ran — " + a.length + " new alert(s)";
}

async function ack(id){
  await j("/api/alerts/ack", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: id})});
  loadAlerts();
}

document.addEventListener("click", e => {
  const b = e.target.closest ? e.target.closest("button[data-ack]") : null;
  if (b) ack(b.dataset.ack);
});


async function loadMaint(){
  try {
    const s = await j("/api/maintenance/stats");
    const fmtBytes = b => b > 1048576 ? (b/1048576).toFixed(1)+" MB" : b > 1024 ? (b/1024).toFixed(1)+" KB" : b+" B";
    const cards = [
      ["records", s.records, "stored commands"],
      ["alerts", s.alerts, "persisted alerts"],
      ["db size", fmtBytes(s.db_bytes), "on disk"],
      ["oldest", s.oldest ? fmt(s.oldest) : "–", "first record"],
      ["newest", s.newest ? fmt(s.newest) : "–", "last record"]
    ];
    $("maint-stats").innerHTML = cards.map(c =>
      '<div class="stat"><div class="n">' + c[1] + '</div><div class="l">' + c[0] + '</div>' +
      (c[2] ? '<div class="sub">' + c[2] + '</div>' : '') + '</div>').join("");
    $("maint-msg").textContent = "";
    bump("maint");
  } catch (e) { $("maint-msg").textContent = "error: " + e.message; }
}

async function doRetain(){
  const days = parseInt($("retain-days").value, 10);
  $("maint-msg").textContent = "Pruning records older than " + days + " days…";
  try {
    const r = await j("/api/maintenance/retain", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({days: days})});
    $("maint-msg").textContent = "Removed " + r.removed + " records, kept " + r.kept + " (cutoff " + r.cutoff + ")";
    loadMaint();
  } catch (e) { $("maint-msg").textContent = "error: " + e.message; }
}

async function doVacuum(){
  $("maint-msg").textContent = "Running VACUUM — may take a few seconds…";
  try {
    const r = await j("/api/maintenance/vacuum", {method: "POST"});
    $("maint-msg").textContent = "VACUUM complete — DB " + (r.before_bytes !== undefined ? (r.before_bytes/1048576).toFixed(1)+" MB → " + (r.after_bytes/1048576).toFixed(1)+" MB" : "");
    loadMaint();
  } catch (e) { $("maint-msg").textContent = "error: " + e.message; }
}

async function loadReports(){
  try {
    const days = parseInt($("report-days").value, 10);
    const fmt = $("report-format").value;
    $("report-msg").textContent = "";
    bump("reports");
  } catch (e) { $("report-msg").textContent = "error: " + e.message; }
}

function reportUrl(){
  const days = parseInt($("report-days").value, 10);
  const fmt = $("report-format").value;
  return "/api/report?format=" + fmt + "&days=" + days;
}

function downloadReport(){
  try {
    const fmt = $("report-format").value;
    const days = parseInt($("report-days").value, 10);
    const a = document.createElement("a");
    a.href = reportUrl();
    a.download = "retrace-report-" + days + "d." + (fmt === "csv" ? "csv" : fmt === "json" ? "json" : "html");
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    $("report-msg").textContent = "Download started — " + fmt.toUpperCase() + " · " + days + " days";
  } catch (e) { $("report-msg").textContent = "error: " + e.message; }
}

function previewReport(){
  window.open(reportUrl(), "_blank");
}

/* ---------- settings / control plane ---------- */
function cfgFromUI(){
  return {model: {
    provider: $("cfg-provider").value,
    model: $("cfg-model").value,
    url: $("cfg-url").value,
    timeout_s: parseInt($("cfg-timeout").value, 10) || 60,
    max_input_chars: parseInt($("cfg-maxchars").value, 10) || 12000,
    enabled: $("cfg-enabled").checked
  }};
}
function cfgToUI(c){
  const m = c.model || {};
  $("cfg-provider").value = m.provider || "null";
  $("cfg-model").value = m.model || "";
  $("cfg-url").value = m.url || "";
  $("cfg-timeout").value = m.timeout_s || 60;
  $("cfg-maxchars").value = m.max_input_chars || 12000;
  $("cfg-enabled").checked = !!m.enabled;
}
async function loadSettings(){
  try {
    const c = await j("/api/settings/config");
    cfgToUI(c);
    const d = await j("/api/settings/detectors");
    renderDetectors(d.rules || []);
    const r = await j("/api/settings/remotes");
    renderRemotes(r.hosts || []);
    $("settings-msg").textContent = "";
    bump("settings");
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
function renderDetectors(rules){
  const body = $("det-rules");
  if (!rules.length){ body.innerHTML = '<tr><td colspan="6" class="empty">No rules.</td></tr>'; return; }
  body.innerHTML = rules.map(r =>
    "<tr><td>" + esc(r.name) + (r.enabled ? "" : ' <span class="muted">(off)</span>') + "</td>" +
    "<td><span class='sev " + esc(r.severity) + "'>" + esc(r.severity) + "</span></td>" +
    "<td class='cmd' style='max-width:280px'>" + esc(r.match) + "</td>" +
    "<td>" + (r.window_s ? r.window_s + "s" : "–") + "</td>" +
    "<td>" + (r.threshold || 1) + "</td>" +
    "<td><button data-dtoggle='" + esc(r.name) + "'>" + (r.enabled ? "disable" : "enable") + "</button> " +
    "<button data-ddel='" + esc(r.name) + "'>del</button></td></tr>").join("");
}
function renderRemotes(hosts){
  const body = $("rm-list");
  if (!hosts.length){ body.innerHTML = '<tr><td colspan="5" class="empty">No remotes.</td></tr>'; return; }
  body.innerHTML = hosts.map(h =>
    "<tr><td>" + esc(h.name) + "</td><td>" + esc(h.host) + "</td><td>" + esc(h.user || "–") + "</td><td>" + h.port + "</td>" +
    "<td><button data-rtest='" + esc(h.name) + "'>test</button> <button data-rdel='" + esc(h.name) + "'>remove</button></td></tr>").join("");
}
async function saveConfig(){
  try {
    const r = await j("/api/settings/config", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({patch: cfgFromUI()})});
    $("settings-msg").textContent = "config saved — provider " + ((r.model || {}).provider);
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function addDetector(){
  const rule = {
    name: $("det-name").value.trim(),
    severity: $("det-sev").value,
    match: $("det-match").value.trim(),
    window_s: null, threshold: 1,
    message: "Pattern detected: '{}'",
    description: "Custom rule added from UI"
  };
  if (!rule.name || !rule.match){ $("settings-msg").textContent = "name + regex required"; return; }
  try {
    const r = await j("/api/settings/detectors/add", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({rule: rule})});
    $("settings-msg").textContent = r.error || ("added " + r.name);
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function testDetector(){
  try {
    const r = await j("/api/settings/detectors/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({match: $("det-match").value, sample: $("det-sample").value})});
    $("det-test-result").textContent = r.error || (r.match ? "✓ matches" : "✗ no match");
  } catch (e) { $("det-test-result").textContent = "error: " + e.message; }
}
async function toggleDetector(name){
  try {
    await j("/api/settings/detectors/update", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: name, patch: {enabled: false}})});
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function deleteDetector(name){
  try {
    await j("/api/settings/detectors/delete", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: name})});
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function addRemote(){
  try {
    const body = {name: $("rm-name").value.trim(), host: $("rm-host").value.trim(), user: $("rm-user").value.trim(), port: parseInt($("rm-port").value, 10) || 22};
    if (!body.name || !body.host){ $("settings-msg").textContent = "name + host required"; return; }
    const r = await j("/api/settings/remotes/add", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    $("settings-msg").textContent = r.error || ("added " + r.remote.name);
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function testRemote(name){
  try {
    const r = await j("/api/settings/remotes/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: name})});
    $("settings-msg").textContent = name + ": " + (r.error || (r.ok ? "OK — " + r.message : "FAILED — " + r.message));
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function removeRemote(name){
  try {
    await j("/api/settings/remotes/remove", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: name})});
    loadSettings();
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function runAction(action, arg){
  try {
    const r = await j("/api/actions/" + action, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({arg: arg})});
    $("settings-msg").textContent = action + " → " + (r.job_id ? "job " + r.job_id + " running…" : JSON.stringify(r));
    if (r.job_id) setTimeout(loadJobResult, 2500, r.job_id);
  } catch (e) { $("settings-msg").textContent = "error: " + e.message; }
}
async function loadJobResult(jid){
  try {
    const r = await j("/api/jobs/" + jid);
    $("job-result").textContent = jid + " → " + r.status + (r.error ? " ERR: " + r.error : " " + JSON.stringify(r.result).slice(0, 300));
  } catch (e) { $("job-result").textContent = "error: " + e.message; }
}
document.addEventListener("click", e => {
  const t = e.target.closest ? e.target.closest("button[data-dtoggle]") : null;
  if (t) toggleDetector(t.dataset.dtoggle);
  const d = e.target.closest ? e.target.closest("button[data-ddel]") : null;
  if (d) deleteDetector(d.dataset.ddel);
  const rt = e.target.closest ? e.target.closest("button[data-rtest]") : null;
  if (rt) testRemote(rt.dataset.rtest);
  const rd = e.target.closest ? e.target.closest("button[data-rdel]") : null;
  if (rd) removeRemote(rd.dataset.rdel);
});

function switchTab(name){
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "dashboard") loadDashboard();
  if (name === "timeline") loadRecords();
  if (name === "alerts") loadAlerts();
  if (name === "remotes") loadRemotes();
  if (name === "maint") loadMaint();
  if (name === "reports") loadReports();
  if (name === "settings") loadSettings();
}

function fmt(ts){
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString();
}
function esc(s){
  return String(s || "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
function bump(tab){
  const active = document.querySelector(".tab.active").dataset.tab;
  if (tab === active) $("refresh").textContent = "updated " + new Date().toLocaleTimeString();
}

setInterval(() => {
  const active = document.querySelector(".tab.active").dataset.tab;
  if (active === "dashboard") loadDashboard();
  if (active === "timeline") loadRecords();
  if (active === "alerts") loadAlerts();
  if (active === "remotes") loadRemotes();
  if (active === "maint") loadMaint();
  if (active === "reports") loadReports();
  if (active === "settings") loadSettings();
}, 20000);

$("lock").textContent = "live";
switchTab("dashboard");
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RetraceUI/0.2"

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
                self._json(stats_for_api(conn))
            elif path == "/api/analytics/dashboard":
                days = int(qs.get("days", ["30"])[0])
                days = max(1, min(days, 365))
                import time as _t
                now = _t.time()
                if (_dashboard_cache["data"] is None or _dashboard_cache["days"] != days
                        or now - _dashboard_cache["ts"] > _DASH_TTL):
                    _dashboard_cache["data"] = analytics_dashboard(conn, days=days)
                    _dashboard_cache["days"] = days
                    _dashboard_cache["ts"] = now
                self._json(_dashboard_cache["data"])
            elif path == "/api/records":
                limit = int(qs.get("limit", ["100"])[0])
                q = qs.get("q", [""])[0]
                self._json(records_for_api(conn, limit=limit, q=q))
            elif path == "/api/alerts":
                since_min = int(qs.get("since", ["1440"])[0])
                self._json(alerts_for_api(conn, since_minutes=since_min))
            elif path == "/api/remotes":
                self._json(remotes_for_api(conn))
            elif path == "/api/detect":
                self._json(run_detect(conn))
            elif path.startswith("/api/maintenance/stats"):
                import retrace.db as db
                st = db.db_stats()
                self._json(st)
            elif path.startswith("/api/report"):
                from . import reports
                fmt = qs.get("format", ["html"])[0]
                days = int(qs.get("days", ["30"])[0])
                days = max(1, min(days, 365))
                if fmt == "csv":
                    body = reports.make_csv(conn, since_days=days).encode("utf-8")
                    self._send(200, body, "text/csv; charset=utf-8")
                elif fmt == "json":
                    body = reports.make_json(conn, since_days=days).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                else:
                    body = reports.make_html_report(conn, since_days=days).encode("utf-8")
                    self._send(200, body, "text/html; charset=utf-8")
            elif path == "/api/settings/config":
                from . import settings
                self._json(settings.get_config())
            elif path == "/api/settings/detectors":
                from . import settings
                self._json(settings.get_detectors())
            elif path == "/api/settings/remotes":
                from . import settings
                self._json({"hosts": settings.get_remotes()})
            elif path.startswith("/api/jobs/"):
                from . import settings
                jid = path.rsplit("/", 1)[-1]
                self._json(settings.job_status(jid))
            elif path == "/api/actions/stats":
                from . import settings
                self._json(settings.action_stats())
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
            elif parsed.path.startswith("/api/maintenance/retain"):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                days = int(body.get("days", 60))
                self._json(retain(days=days))
            elif parsed.path.startswith("/api/maintenance/vacuum"):
                self._json(vacuum())
            elif parsed.path == "/api/settings/config":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.update_config(body.get("patch", body)))
            elif parsed.path == "/api/settings/config/reset":
                from . import settings
                self._json(settings.reset_config())
            elif parsed.path == "/api/settings/detectors/add":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.add_detector(body.get("rule", {})))
            elif parsed.path == "/api/settings/detectors/update":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.update_detector(body.get("name", ""), body.get("patch", {})))
            elif parsed.path == "/api/settings/detectors/delete":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.delete_detector(body.get("name", "")))
            elif parsed.path == "/api/settings/detectors/test":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.test_detector(body.get("match", ""), body.get("sample", "")))
            elif parsed.path == "/api/settings/remotes/add":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.add_remote(body.get("name", ""), body.get("host", ""),
                                              body.get("user", ""), int(body.get("port", 22) or 22)))
            elif parsed.path == "/api/settings/remotes/remove":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.remove_remote(body.get("name", "")))
            elif parsed.path == "/api/settings/remotes/test":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                self._json(settings.test_remote(body.get("name", "")))
            elif parsed.path.startswith("/api/actions/"):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                from . import settings
                action = parsed.path.rsplit("/", 1)[-1]
                arg = body.get("arg")
                if action == "ingest":
                    res = settings.start_job("ingest", settings.action_ingest,
                                             history=(arg in (None, "history")),
                                             flight=(arg == "flight"),
                                             ps=(arg == "ps"))
                elif action == "detect":
                    res = settings.start_job("detect", settings.action_detect)
                elif action == "collect":
                    res = settings.start_job("collect", settings.action_collect_remote,
                                             name=None, all_hosts=(arg == "all"))
                elif action == "win-events":
                    res = settings.start_job("win-events", settings.action_win_events)
                elif action == "agent-cycle":
                    res = settings.start_job("agent-cycle", settings.action_agent_cycle)
                else:
                    res = {"error": "unknown action"}
                self._json(res)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            import traceback as _tb; _tb.print_exc()
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


def analytics_dashboard(conn, days: int = 30) -> dict:
    """Everything the dashboard charts need, in one call."""
    from . import analytics

    return {
        "overview": analytics.overview(conn),
        "series": analytics.time_series(conn, bucket="day", since_days=days),
        "hourly": analytics.hourly_profile(conn, since_days=days),
        "weekday": analytics.weekday_profile(conn, since_days=days),
        "usage": analytics.command_usage(conn, limit=25, since_days=days),
        "top_commands": analytics.top_commands(conn, limit=20, since_days=days),
        "heatmap": analytics.activity_heatmap(conn, days=days),
    }


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


def serve(port: int = 55555) -> None:
    """Start the local-only web server. Blocks forever."""
    import os as _os
    server = None
    for _attempt in range(3):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as _e:
            print(f"Port {port} in use ({_e}). Trying port {port + 1}...")
            port += 1
    if server is None:
        print("ERROR: could not bind any port. Is another Retrace UI already running?")
        print("  - If a UI is already open, use it: http://127.0.0.1:55555")
        print("  - Or stop the other instance first: taskkill /F /PID <pid>")
        raise SystemExit(1)
    import threading as _th
    def _warm():
        try:
            conn = connect()
            _dashboard_cache["data"] = analytics_dashboard(conn, days=30)
            _dashboard_cache["days"] = 30
            _dashboard_cache["ts"] = time.time()
            conn.close()
        except Exception:
            pass
    _th.Thread(target=_warm, daemon=True).start()
    print(f"Retrace UI: http://127.0.0.1:{port}  (localhost only — no network exposure)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

def _main() -> None:
    """CLI entrypoint: retrace webui --port N"""
    import argparse
    p = argparse.ArgumentParser(description="Retrace web UI (localhost only)")
    p.add_argument("--port", type=int, default=55555, help="Port to bind (default 55555)")
    args = p.parse_args()
    serve(args.port)


if __name__ == "__main__":
    _main()
