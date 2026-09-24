# Detection Rules

The detectors engine is the **"intelligent code, not agentic"** layer of
Retrace. Every rule is a pure function over the captured command stream — no
LLM, no network, no external calls. Rules are declarative and user-extensible
via `~/.config/retrace/detectors.json`.

## Built-in Rules

| Rule | Severity | Pattern (abbrev.) | Window | Threshold |
|------|----------|-------------------|--------|-----------|
| `pipe-to-shell` | high | `\| sh`, `\| bash`, `\| python`, `\| perl` | 300 s | 1 |
| `curl-pipe-shell` | critical | `(curl\|wget) ... \| sh` — supply-chain RCE | 300 s | 1 |
| `secret-in-command` | high | `token/secret/password/api_key=...` (12+ chars) | — | 1 |
| `privilege-escalation` | medium | `sudo -i`, `sudo -s`, `su - <user>`, `sudo su` | — | 1 |
| `destructive-command` | critical | `rm -rf /`, `dd if=... of=/dev/`, fork bomb | — | 1 |
| `exfiltration-pattern` | high | `nc -e`, `bash -i >& /dev/tcp/`, `base64 -d \| sh` | — | 1 |
| `auth-failure-spike` | medium | permission denied / auth failed / access denied | 600 s | 3 |
| `long-running-command` | info | any command | — | 1 (≥ 300 s) |
| `kill-process-family` | medium | `killall`/`pkill` with `-9` | — | 1 |
| `suspicious-download` | medium | `wget`/`curl` of `.exe .msi .sh .bin .deb .rpm .appimage` | — | 1 |

## How Rules Work

Each rule:

| Field | Meaning |
|-------|---------|
| `name` | Unique id (kebab-case) |
| `severity` | `info` \| `medium` \| `high` \| `critical` |
| `description` | What it looks for |
| `match` | Regex applied to the command text (case-insensitive) |
| `window_s` | Optional time window (seconds) for counting rules |
| `threshold` | Min occurrences within the window to fire |
| `min_duration_ms` | Optional: only match commands running at least this long |
| `message` | Human-readable alert body (may use `{count}`) |

Counting rules (e.g. `auth-failure-spike`) group hits within `window_s`
seconds and fire when the count reaches `threshold`. Single-shot rules fire
on the first match.

## Running Detection

```bash
retrace detect                    # last 60 min, up to 2000 rows
retrace detect --since 1440       # last 24 h
retrace detect --limit 10000      # evaluate more rows
```

Alerts are persisted to the `alerts` table and surfaced in the web UI. The
agent and watch daemon run detection every cycle automatically.

## Extending Rules

Write your own rules to `~/.config/retrace/detectors.json`. The engine merges
user rules over built-ins: you can **disable**, **override**, or **add** rules
by name.

```bash
# Generate an example config to start from
retrace detect --init-config
# → Wrote example config to ~/.config/retrace/detectors.json
```

### Example: disable a built-in, override one, add one

```json
[
  {
    "name": "long-running-command",
    "enabled": false
  },
  {
    "name": "auth-failure-spike",
    "severity": "high",
    "threshold": 2,
    "message": "{} auth failures in 10 minutes (tightened)"
  },
  {
    "name": "tmux-detected",
    "severity": "info",
    "description": "Example: flag any use of tmux",
    "match": "\\btmux\\b",
    "window_s": null,
    "threshold": 1,
    "message": "tmux used: '{}'"
  }
]
```

### Rule authoring tips

- Regexes are case-insensitive (`re.IGNORECASE`)
- Escape backslashes in JSON (`\\b` for `\b`)
- `window_s: null` means "no time window" (match any row)
- `message` supports one `{}` for the first matching command and `{count}`
- A malformed config never breaks the engine — it logs a warning and uses
  the built-ins

## Alert Lifecycle

1. Detection (CLI, agent, watch, or web UI) produces alert dicts
2. Alerts are inserted into the `alerts` table with a unique id
3. The web UI lists unacknowledged alerts; clicking **ack** sets `acked=1`
4. The stats endpoint counts unacknowledged alerts

## Severity Ordering

Alerts sort critical → high → medium → info, then by recency. The web UI
colors them accordingly (`critical` red, `high` orange, `medium` yellow,
`info` blue).