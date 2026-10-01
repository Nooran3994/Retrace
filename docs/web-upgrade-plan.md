# Retrace Web Upgrade — Implementation Plan

**Status:** Planning · **Owner:** Elan + SCAAI · **Target:** v0.2.0 of the web layer

---

## 1. Goals (from Elan)

1. **Analytics** — recorded data analyzed with charts and diagrams.
2. **Reports** — downloadable, detailed, well-structured analysis reports.
3. **UX** — user-friendly interface.
4. **Config from UI** — every CLI capability available through the settings UI, no terminal required.

## 2. Current State (verified from source)

| Layer | File | What exists |
|---|---|---|
| Web server | `retrace/webui.py` | Single self-contained HTML page, 5 routes (`/api/stats`, `/api/records`, `/api/alerts`, `/api/detect`, `/api/alerts/ack`). `remotes_for_api()` exists but **is not wired to any route**. |
| Storage | `retrace/db.py` | Append-only SQLite: `commands` + `alerts` tables. No analytics queries yet. |
| Rules | `retrace/detectors.py` | 10 built-in rules, user-extensible via `~/.config/retrace/detectors.json`. |
| Config | `retrace/models.py` | `~/.config/retrace/config.json` with `load_config/save_config/set_config` (model provider settings). |
| CLI | `retrace/cli.py` | 12 commands: ingest, hook, win-events, remote (add/list/remove/test/collect/script), search, stats, detect, web, watch, agent, export. |
| Watch | `retrace/watch.py` | Periodic ingest + detect loop; interval is a CLI arg only (not persisted). |

**Key constraints (integrity contract, must not break):**
- Binds strictly to `127.0.0.1` — never `0.0.0.0`.
- No external assets, no CDN, no telemetry. All frontend assets served from memory.
- Capture/detect loop never depends on a model. Model analysis is opt-in.
- Only redacted text is ever sent to a model.

## 3. Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Charting | **Hand-rolled inline SVG/Canvas** (no library) | CDN ban means no Chart.js/echarts. Hand-rolled gives full control, zero supply chain, ~1 file. |
| Frontend | Rebuild as **tabbed single-page app** served from memory (still one HTML file, or split into a small JS bundle served by the same handler) | Keeps the "no external assets" contract; maintainable. |
| Analytics | New module `retrace/analytics.py` — SQL aggregation | Fast, deterministic, no schema migration needed (computed on the fly). |
| Reports | New module `retrace/reports.py` — self-contained HTML + CSV/JSON | HTML report embeds inline CSS + inline SVG charts → printable to PDF via browser. |
| Config surface | Extend `config.json` schema (watch interval, UI prefs, collector flags) + full CRUD for `detectors.json` | Single source of truth, atomic writes, validation. |
| CLI parity | Every CLI command exposed as an API action; CLI and UI call the **same functions** | No drift between terminal and UI behavior. |
| Slow actions | Background job runner with `/api/jobs/status` | `remote collect`, `ingest`, `detect` can take seconds; don't block the UI. |
| DB schema | **No migration needed** — analytics computed via SQL over existing tables | Zero risk to append-only integrity. Optional `meta` table for UI prefs only. |

## 4. Phases

### Phase 1 — Analytics Engine (backend)
**Deliverable:** `retrace/analytics.py` + analytics API endpoints.

Queries to implement (all SQL, over `commands` + `alerts`):
- Time series: records per hour/day (configurable bucket) → line chart
- Top commands (normalized: strip args, count) → bar chart
- Top working directories → bar chart
- Source distribution (history/flight/ps/win-events/remote) → donut
- Shell distribution (bash/zsh/powershell/cmd) → donut
- Alert trend over time + alerts by severity → line + stacked bar
- Entropy-flagged records count, avg/max duration, exit-code failures → stat cards
- Activity heatmap (hour × weekday) → calendar heatmap

New routes:
```
GET /api/analytics/summary     → stat cards payload
GET /api/analytics/timeseries  → ?bucket=hour|day&range=24h|7d|30d|all
GET /api/analytics/top         → ?by=command|cwd&limit=10
GET /api/analytics/dist        → ?by=source|shell
GET /api/analytics/alerts      → ?range=7d|30d
GET /api/analytics/heatmap     → hour×weekday counts
```

### Phase 2 — Dashboard & Charts (frontend)
**Deliverable:** Rebuilt UI with tabs + hand-rolled SVG chart components.

- Tab layout: **Dashboard · Timeline · Alerts · Remotes · Settings**
- Dashboard: stat cards row + line chart (activity) + donut (sources) + bar (top commands) + heatmap
- SVG chart primitives: `lineChart()`, `barChart()`, `donutChart()`, `heatmap()` — ~300 lines, dependency-free
- Timeline: search, filters (source, shell, date range), pagination, detail row (cwd, exit code, duration, session)
- Alerts: filter by severity/acked, ack from list, alert detail with samples
- Responsive CSS, consistent dark theme, keyboard-focusable controls

### Phase 3 — Reports (backend + frontend)
**Deliverable:** `retrace/reports.py` + Report tab.

- **HTML report** (self-contained): header (range, generated-at, filters), summary stat cards, inline SVG charts, top-commands table, alerts table, full timeline table. Inline CSS → clean print → "Save as PDF" via browser.
- **CSV export**: filtered records (same filters as timeline).
- **JSON export**: full structured payload (records + stats + alerts) for machine use.
- Routes:
```
GET /api/report?format=html&range=7d&source=all&q=...
GET /api/report?format=csv|json&range=...&q=...
```
- Report tab in UI: pick range/filters → preview → Download HTML / CSV / JSON buttons.

### Phase 4 — Config & Control Plane (backend)
**Deliverable:** Config API + actions API + job runner.

- Extend `config.json` schema:
```json
{
  "model": { ... existing ... },
  "watch": { "interval_s": 60, "enabled": false },
  "collectors": { "history": true, "flight": true, "ps": true, "win_events": true, "remote": true },
  "ui": { "theme": "dark", "page_size": 100 }
}
```
- Routes:
```
GET  /api/config              → full config
PUT  /api/config              → validated atomic write
GET  /api/detectors           → merged rules (built-ins + user)
PUT  /api/detectors           → save user rule overrides (enable/disable/edit/add)
POST /api/remotes             → add host {name,host,user,port}
GET  /api/remotes             → list + per-host record counts (wire existing remotes_for_api)
DELETE /api/remotes/<name>    → remove host
POST /api/remotes/<name>/test → SSH connectivity test
POST /api/remotes/<name>/collect → collect now
POST /api/remotes/collect-all
POST /api/actions/ingest      → {history,flight,ps} flags
POST /api/actions/win-events  → {minutes}
POST /api/actions/detect      → run + persist alerts
POST /api/actions/watch/start | /stop | /once
POST /api/actions/hook/install | /install-ps
GET  /api/jobs/<id>           → job status/progress
```

### Phase 5 — Settings UI (frontend)
**Deliverable:** Settings tab — full command surface without terminal.

| Section | Controls |
|---|---|
| Model provider | provider dropdown (null/ollama/openai), URL, model tag, timeout, enabled toggle, system prompt editor, "Test connection" |
| Detectors | list all rules with severity badges; toggle enabled; edit regex/severity/threshold/window; add new rule; delete override; "Test regex" box |
| Remotes | add form (name/host/user/port), list with records + Test / Collect / Remove buttons, Collect All |
| Watch daemon | interval input, Start / Stop / Run once, last-cycle summary |
| Collectors | checkboxes (history/flight/ps/win-events/remote) + "Run ingest now" |
| Data | DB path display, Export buttons (HTML/CSV/JSON), record count, "Run detect now" |

### Phase 6 — Hardening & Polish
- Security review: every new endpoint stays localhost-only; validate all inputs; no path traversal on export filenames; atomic config writes (write-tmp-then-rename) so a crash can't corrupt config; detectors already tolerate bad JSON — keep that.
- Slow actions run via job runner (threading) with status polling — never block the HTTP thread.
- Responsive layout (works on small screens), prefers-reduced-motion respected.
- Tests (`tests/`): analytics queries, report generation (HTML/CSV/JSON), config CRUD validation, detector rule CRUD, job runner.

### Phase 7 — Docs & Ship
- Update `docs/architecture.md`, `docs/usage.md`, `docs/web-upgrade-plan.md` → move to `docs/` as the design record.
- Update README with new UI capabilities.
- Bump version → `v0.2.0`. Commit per phase (Conventional Commits).

## 5. Dependencies & Risks

| Risk | Mitigation |
|---|---|
| Hand-rolled charts = more code | Keep chart primitives small and generic; test with fixed datasets; reuse across dashboard/report. |
| Watch interval change needs restart | Watch loop re-reads `config.json` each cycle → interval changes apply on next cycle; UI shows live status. |
| Slow actions block HTTP thread | Job runner background thread + status endpoint. |
| Config corruption | Atomic writes + validation + fallback to defaults (same pattern as `load_config`). |
| Scope creep on UI | Tabbed structure keeps each surface isolated; Settings section maps 1:1 to CLI commands. |

## 6. Acceptance Criteria

1. Dashboard renders ≥5 chart types from live DB data, no external assets.
2. Report tab produces a printable HTML report + CSV + JSON in one click each.
3. Every CLI command (ingest, hook, win-events, remote, detect, watch, agent, export, search, stats) is triggerable from the UI.
4. Detector rules and model config editable from UI, persisted to disk, engine still resilient to bad config.
5. `127.0.0.1` binding unchanged; no CDN/telemetry; existing tests pass; new tests added.
6. UI usable on mobile width; keyboard navigable.