# Reports & Export

Since v0.2.0, Retrace can generate **detailed, well-structured reports** from recorded data — downloadable from the web UI or via API.

## Report formats

| Format | Contents | Best for |
|---|---|---|
| **HTML** | Self-contained page: overview stats, time series, top commands, failures, alerts — inline CSS + inline SVG | Printing to PDF (Ctrl+P), sharing, archiving |
| **CSV** | Raw filtered command records | Spreadsheets, further analysis |
| **JSON** | Full analytics payload | Machine consumption, pipelines |

All three are generated locally — nothing is uploaded anywhere.

## Downloading from the UI

**Reports tab:**

1. Pick a period: **7 / 30 / 90 days**
2. Pick a format: **HTML / CSV / JSON**
3. Click **Download** — the browser saves the file
4. **Preview** opens the report in a new tab

## API

```bash
# HTML report (last 30 days)
curl -o report.html "http://127.0.0.1:8765/api/report?format=html&days=30"

# CSV export
curl -o report.csv "http://127.0.0.1:8765/api/report?format=csv&days=30"

# JSON export
curl -o report.json "http://127.0.0.1:8765/api/report?format=json&days=30"
```

## What's in the HTML report

- **Overview** — total records, hosts, sessions, alerts, failure rate
- **Activity** — hourly/weekly/daily charts (inline SVG)
- **Top commands** — most frequent commands and normalized usage
- **Failures** — exit-code distribution, p50/p95/p99 durations
- **Alerts** — breakdown by rule and severity
- **Sources** — shell history vs flight recorder vs PowerShell vs Windows Events

## Performance

Reports share the summary-backed analytics path, so generation is fast even on large databases (1.1M+ rows). The first request after server start may take a few seconds while the summary tables refresh; subsequent requests are near-instant.