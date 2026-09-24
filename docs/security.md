# Security Model

Retrace's security posture is best summarized as: **the intelligence comes
from respecting the boundary of your machine.** The system is designed so that
nothing sensitive leaves the device, and so that the tool itself cannot be
turned into a data exfiltration channel.

## Threat Model

| Threat | How Retrace Mitigates It |
|--------|--------------------------|
| Data exfiltration | Zero outbound network calls in the core path. Web UI binds to `127.0.0.1` only. Export requires explicit user action. |
| Secret leakage | Redaction at capture — secrets masked *before* storage, original discarded. Entropy-flagged tokens are flagged, never stored. |
| Unauthorized access to the DB | File permissions locked to the current user (`chmod 700` on parent dir, `0600` on config/state files). Optional encryption-at-rest is on the roadmap. |
| Malicious command execution | Retrace only *observes*; it never executes captured commands. Replay is display-only. |
| Supply-chain compromise | Stdlib-only (no pip dependencies). Installer is idempotent and inspectable. No post-install scripts. |
| Remote device compromise | Agentless collection (nothing installed on remotes). SSH-only transport, `BatchMode=yes`, no stored credentials. |
| False sense of security | The UI labels the data path: **LOCAL ONLY** badge; model analysis is off until explicitly enabled. |

## Redaction Engine

Every captured line passes through `redact.py` **before** anything is stored.
The pipeline:

1. **Whole-line assignments** — `export TOKEN=...` → `export TOKEN=[REDACTED]`
   (key name preserved, value masked)
2. **Known secret shapes:**
   - GitHub classic PAT: `ghp_[A-Za-z0-9]{20,}`
   - GitHub fine-grained PAT: `github_pat_[A-Za-z0-9_]{20,}`
   - GitHub OAuth: `gho_[A-Za-z0-9]{20,}`
   - Slack tokens: `xox[baprs]-...`
   - AWS access key IDs: `AKIA[0-9A-Z]{16}`
   - OpenAI-style keys: `sk-[A-Za-z0-9]{20,}`
   - JWTs: `eyJ...` three-part base64url
3. **Secret-ish key/value pairs** — any of
   `api_key|apikey|secret|token|passwd|password|pwd|auth|credential|private_key|access_key`
   followed by `=` or `:` is masked
4. **Bearer sequences** — `Bearer <token>` masked

### Entropy flagging

`is_high_entropy()` computes Shannon entropy per byte for every token of 12+
characters. Tokens with entropy ≥ 3.5 look random (secret-like). The **flag**
is stored in the `entropy` column; the **text is not**.

### Design rule

> Retrace never stores raw secrets. Patterns are conservative — we over-redact
> rather than leak — and the original text is discarded, not reversible.

## Integrity Guarantees

1. **Capture/detect never depends on a model.** If no provider is configured
   or reachable, Retrace runs fully deterministic.
2. **Model analysis is opt-in** (`retrace config set model.provider ollama`
   + `model.enabled true`) and only ever sees **redacted** text.
3. **Model output is advisory.** It may insert insight rows into the `alerts`
   table but can never modify or delete `commands` rows.
4. **Append-only storage.** Rows are immutable once written; replay timelines
   stay honest.
5. **No telemetry, no analytics, no CDN.** The web UI is a single HTML page
   served from memory.

## Network Surface

| Component | Network behavior |
|-----------|------------------|
| Capture (history/flight/ps/win-events) | None — local files and local APIs only |
| Detection | None — pure functions over local rows |
| Web UI | Listens on `127.0.0.1` only; never binds `0.0.0.0` |
| Remote collection | SSH outbound only, opt-in, per registered host |
| Model analysis | Localhost HTTP to Ollama/LM Studio only, opt-in |

## Data Paths

| Kind | Location |
|------|----------|
| SQLite DB (Linux/WSL) | `~/.local/share/retrace/rec.db` |
| SQLite DB (Windows) | `%LOCALAPPDATA%\retrace\rec.db` |
| Config | `~/.config/retrace/config.json` (0600) |
| Detector rules | `~/.config/retrace/detectors.json` |
| Remote hosts | `~/.config/retrace/hosts.json` (0600) |
| Flight spool | `~/.local/share/retrace/spool/flight.jsonl` |
| Agent state | `~/.local/share/retrace/state.json` (0600) |
| PS transcripts | `%LOCALAPPDATA%\retrace\transcripts\*.txt` |

## Operational Hardening Tips

- **Lock the DB further:** `chmod 600 ~/.local/share/retrace/rec.db`
- **Encrypt at rest (roadmap):** SQLCipher integration is planned; until then,
  full-disk encryption (BitLocker / LUKS) covers the DB file
- **Remote collection:** use dedicated SSH keys with `no-agent-forwarding` and
  allow only read commands via `authorized_keys` command restrictions
- **Model provider:** keep `url` pointed at `127.0.0.1`; never point it at a
  public endpoint unless you explicitly accept that tradeoff