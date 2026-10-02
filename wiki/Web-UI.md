# Web UI

Since v0.2.0, Retrace ships a full local web UI — a tabbed single-page app served from your machine. Everything runs locally; nothing is fetched from a CDN.

## Starting the UI

```bash
retrace web
```

Opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). If your OS blocks the default port (common on Windows — `WinError 10013`), use:

```bash
retrace web --port 55555
```

## Tabs

### Dashboard
Stat cards (records, hosts, sessions, alerts, failures) and five dependency-free SVG charts:
- Activity heatmap (24h × days)
- Hourly activity bars
- Weekday activity bars
- Daily command series
- Top-command usage bars

Charts are hand-rolled SVG — no Chart.js, no CDN. The local-only contract holds: nothing leaves your machine.

### Timeline
Searchable command history with debounced search. Type a pattern and results filter instantly.

### Alerts
Run detectors and acknowledge alerts. Each alert shows the rule, severity, and matched command.

### Remotes
Registered SSH hosts with per-host record counts. Add/remove hosts here (or via Settings).

### Maintenance
- **DB stats** — records, alerts, DB size on disk, oldest/newest record dates
- **Retention** — prune records older than 30/60/90/180 days
- **Vacuum** — reclaim disk space freed by retention

### Reports
Download analyzed reports:
- **HTML** — self-contained, printable to PDF (Ctrl+P)
- **CSV** — raw filtered records
- **JSON** — full analytics payload

Period selector: 7 / 30 / 90 days.

### Settings
The terminal-free control surface:
- **Model config** — provider, URL, model tag, timeout, enable/disable
- **Detectors** — list, add, update, delete, **live regex test**
- **Remotes** — add / remove / test SSH hosts
- **Actions** — one-click: ingest history/flight/ps, run detectors, agent cycle, win-events, collect remotes (run in background jobs, pollable)

## API

The UI is backed by a small JSON API on the same port:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/analytics/dashboard?days=N` | GET | Dashboard payload (cached 60s) |
| `/api/commands?q=&limit=` | GET | Search commands |
| `/api/alerts` | GET | List alerts |
| `/api/remotes` | GET | List remotes |
| `/api/settings/config` | GET/POST | Read/write config |
| `/api/settings/detectors` | GET | List detectors |
| `/api/settings/detectors/add` | POST | Add detector |
| `/api/settings/detectors/test` | POST | Test regex |
| `/api/settings/remotes/add` | POST | Add remote |
| `/api/actions/stats` | POST | Run CLI action as background job |
| `/api/maintenance/stats` | GET | DB stats |
| `/api/maintenance/retain` | POST | Prune old data |
| `/api/maintenance/vacuum` | POST | Vacuum DB |
| `/api/report?format=html\|csv\|json&days=N` | GET | Download report |

## Performance

Dashboard loads are backed by **materialized summary tables** (since v0.2.0):
- Cold loads: ~2s on a 1.1M-row DB (was ~16s)
- Warm loads: ~3ms (60s cache)
- Reports share the same summary-backed path