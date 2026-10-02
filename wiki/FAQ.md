# FAQ

## What is Retrace?

Retrace is a deterministic, offline terminal intelligence tool. It records your terminal activity locally, redacts secrets at the boundary, runs rule-based detection, and gives you a local web UI to analyze charts, download reports, and configure everything from the browser.

## Is Retrace agentic?

No. Retrace is **agentic-free by design**. The core value — capture, redact, store, detect, analyze — is delivered by deterministic, offline code. No LLM is required. Optional local model analysis is opt-in and advisory only.

## Does my data leave my machine?

No. Everything runs locally: capture, redaction, storage, detection, the web UI, and report generation. The web UI binds to `127.0.0.1` and fetches nothing from a CDN. Data leaves your machine only if you explicitly export a report or trigger remote collection.

## Do I need a model / API key?

No. The core works with zero models and zero API calls. Model analysis is an optional add-on that runs against a **local** model server (e.g., Ollama) — never a cloud API.

## Why is it stdlib-only?

No pip dependencies means no supply-chain risk, no venv hassle, and no version drift. The core is Python 3.9+ stdlib only. If a change needs a third-party package, it's proposed in an issue first and likely rejected unless there's a compelling reason.

## How do I start the web UI?

```bash
retrace web
```

Opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). On Windows, if you get `WinError 10013` (security software blocking the port), use `retrace web --port 55555`.

## How do I make the UI fast on a huge database?

Since v0.2.0 the dashboard is backed by materialized summary tables. Cold loads take ~2s on a 1.1M-row DB (was ~16s); warm loads are ~3ms. You can also prune old data from the **Maintenance** tab.

## How do I prune old data?

**Maintenance tab** → pick 30/60/90/180 days → **Prune**. Or via API:

```bash
curl -X POST http://127.0.0.1:8765/api/maintenance/retain \
  -H "Content-Type: application/json" -d '{"days": 60}'
```

## Can I configure everything from the UI?

Yes. Since v0.2.0 the **Settings** tab covers: model config, detector CRUD with live regex testing, remote host management, and one-click CLI actions (ingest, detect, agent cycle, win-events, collect remotes) — all running as background jobs.

## How do I report a security issue?

Privately, via GitHub Security Advisories — not a public issue:
https://github.com/Nooran3994/Retrace/security/advisories

## How do I contribute?

Read [CONTRIBUTING](../CONTRIBUTING.md), follow the [Code of Conduct](../CODE_OF_CONDUCT.md), use Conventional Commits, and open a PR with the [PR template](../.github/PULL_REQUEST_TEMPLATE.md).

## What are the ground rules for contributions?

1. Stdlib only — no new pip dependencies without prior discussion.
2. Local-first — no telemetry, no CDN, no network calls unless explicitly user-triggered.
3. Deterministic over agentic — capture/detect never depends on a model.
4. Redaction-first — new data sources pass through the redaction engine before storage.
5. Append-only — no UPDATE/DELETE paths on the core tables.