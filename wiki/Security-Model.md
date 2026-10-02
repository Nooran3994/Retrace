# Security Model

Retrace is built around one promise: **your terminal data never leaves your machine unless you explicitly export it.**

## Core guarantees

1. **Local-first by default.** Capture, redaction, storage, detection, and the web UI all run locally. The web UI binds to `127.0.0.1` only.
2. **No telemetry.** No analytics beacons, no phone-home, no CDN fetches. The UI is a single-page app served from your machine with zero external resources.
3. **Redaction-first.** Secrets are masked at the ingest boundary — tokens, keys, and high-entropy strings are replaced before storage. The original is discarded, never written to disk.
4. **Append-only storage.** Command and alert rows are immutable once written. No UPDATE/DELETE paths exist on the core tables, so recorded data cannot be silently altered.
5. **Deterministic detection.** Rule-based detectors run offline. Optional model analysis is opt-in and advisory only — and it runs against a local model server, never a cloud API.

## What is redacted

| Pattern | Example |
|---|---|
| API keys / tokens | `ghp_...`, `sk-...`, `AKIA...` |
| Passwords in commands | `git push https://user:PASSWORD@...` |
| High-entropy strings | Shannon-entropy flagging catches random-looking secrets |

## Reporting a vulnerability

**Do not open a public issue.** Report security vulnerabilities privately via GitHub Security Advisories:

https://github.com/Nooran3994/Retrace/security/advisories

See [SECURITY.md](../SECURITY.md) for the disclosure process and supported versions.

## Data retention

The **Maintenance** tab (or `POST /api/maintenance/retain`) prunes records older than a configurable window (30/60/90/180 days). Retention is reportable and safe — it never touches recent data.

## Threat model notes

- **Local attacker with file access** can read the SQLite DB. The DB file permissions are locked, but full protection requires OS-level disk encryption.
- **The web UI** is bound to localhost and intended for single-user local use. Do not expose it to a network without adding authentication.
- **Remote collection** (SSH) is opt-in and explicit — you register hosts yourself; nothing connects outbound automatically.