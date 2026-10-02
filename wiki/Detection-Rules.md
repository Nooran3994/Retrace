# Detection Rules

Retrace ships with deterministic, rule-based detection. Rules match recorded command patterns and emit alerts with a rule ID and severity.

## Built-in rules

| Rule | Pattern (abridged) | Severity |
|---|---|---|
| `pipe-to-shell` | `curl ... \| sh`, `wget ... \| bash` | high |
| `curl-pipe-shell` | `curl -sSL ... \| sudo bash` | high |
| `destructive-command` | `rm -rf /`, `dd if=... of=/dev/sdX` | high |
| `suspicious-download` | `wget`/`curl` to non-HTTPS hosts | medium |
| `credential-in-command` | `password=`, `--token`, `api_key` | high |
| `privilege-escalation` | `sudo su -`, `pkexec` | medium |
| `reverse-shell` | `nc -e /bin/sh`, `bash -i >& /dev/tcp/` | critical |
| `exfil-pattern` | `scp`/`rsync` to unusual hosts, `curl -X POST` with data | medium |
| `history-clear` | `history -c`, `rm .bash_history` | medium |
| `encoded-command` | `base64 -d \| sh`, `echo ... \| base64` | high |

This is the abridged list — the full set lives in the detectors module and is editable from the **Settings** tab.

## Managing rules from the UI

The **Settings → Detectors** tab gives full CRUD:

- **List** — see all rules with pattern, enabled state, severity
- **Add** — create a new rule with a regex pattern
- **Update** — enable/disable or edit a rule
- **Delete** — remove a rule
- **Live regex test** — paste a command and test the pattern before saving

## Rule anatomy

A detector rule has:

```json
{
  "id": "det-1",
  "name": "credential-in-command",
  "pattern": "(?i)(password\\s*=|api[_-]?key|--token)",
  "severity": "high",
  "enabled": true
}
```

- `pattern` — a Python regex matched against the command text
- `severity` — `critical` / `high` / `medium` / `low`
- `enabled` — toggle without deleting

## Running detection

From the CLI:

```bash
retrace detect
```

From the UI: **Alerts → Run detectors**, or **Settings → Actions → run detectors**. Alerts are stored in the append-only DB and can be acknowledged from the **Alerts** tab.

## Adding rules responsibly

The detectors are deterministic by design. When adding a rule:

1. Keep the regex **specific** — broad patterns cause alert fatigue.
2. Use the **live regex test** to verify it matches real commands and doesn't false-positive on benign ones.
3. Prefer `high`/`critical` only for genuinely dangerous patterns.