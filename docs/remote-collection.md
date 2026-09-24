# Remote Collection

Retrace can aggregate terminal history from other devices **without installing
anything on them**. The insight: the logs are already on every machine —
Retrace pushes a small read-only script over SSH that reads existing history
files and emits JSONL, which the local side redacts and ingests into the
shared database, tagged with the remote hostname.

## Workflow

```bash
# 1. Register a host
retrace remote add nas 192.168.1.10 --user admin --port 22

# 2. Verify SSH connectivity (keys only, BatchMode)
retrace remote test nas
# → nas: OK — ok

# 3. Collect from it
retrace remote collect nas
# → nas: 128 loaded, 3 skipped

# 4. Or collect from every registered host
retrace remote collect --all
```

The agent and watch daemon automatically collect from all registered hosts on
every cycle.

## What Gets Collected

The pushed script reads, on the remote:

| Source | Path |
|--------|------|
| bash history | `~/.bash_history` (with optional `#<epoch>` timestamps) |
| zsh history | `~/.zsh_history` (extended `: ts:dur;cmd` format) |
| PowerShell history | `~/.local/share/powershell/PSReadLine/ConsoleHost_history.txt` |
| Flight spool | `~/.local/share/retrace/spool/flight.jsonl` (if the hook is installed) |

Each line is emitted as JSONL to stdout with `{ts, shell, host, cmd}`. The
local side applies the full redaction engine before inserting into the DB.

## Security Model

| Property | Guarantee |
|----------|-----------|
| **Agentless** | Nothing is installed on remotes — nothing persistent, nothing to secure |
| **SSH-only transport** | Encrypted; the script is fed via stdin, never written to disk on the remote |
| **Keys only** | `BatchMode=yes`, `ConnectTimeout=8` — no password prompts, no stored credentials |
| **Read-only on the remote** | The script only *reads* history/spool files; it writes nothing |
| **Redaction at ingest** | Same engine as everything else — secrets masked before hitting the DB |
| **Hosts file locked** | `~/.config/retrace/hosts.json` is chmod 0600 |

## Host Registry

```bash
retrace remote list          # show registered hosts
retrace remote remove nas    # unregister a host
retrace remote script        # print the collector script for manual review
```

Hosts are stored as JSON:

```json
[
  {"name": "nas", "host": "192.168.1.10", "user": "admin", "port": 22}
]
```

## Prerequisites

- SSH key-based auth must already work to the remote
  (`ssh admin@192.168.1.10` succeeds without a password prompt)
- The remote needs `bash` and, for reliable JSON encoding, `python3`
  (falls back to `sed`-based minimal escaping)

## Deduplication

Records are deduped two ways:

1. **Per-run** — a `seen` set of `(host, ts, cmd)` prevents duplicates within
   one collection
2. **Against the DB** — an existing row with the same `host`, `ts`, and
   `command` is skipped

This makes repeated collection idempotent.

## Hardening Tips

- Restrict the SSH key on the remote with a forced command in
  `authorized_keys` so it can only run the collector script
- Use a dedicated read-only user on the remote
- Keep `StrictHostKeyChecking=accept-new` — first-connection trust is
  recorded by SSH itself