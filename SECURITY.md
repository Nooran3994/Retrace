# Security Policy

Retrace is a local-first security tool. Its integrity model matters: the
data it captures is personal, and the tool's value depends on being
trustworthy. Please report vulnerabilities responsibly.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ (current) |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead:

- Open a **private vulnerability report** via GitHub's Security tab:
  `https://github.com/Nooran3994/Retrace/security/advisories`
- Or email the maintainer directly (address in the GitHub profile).

Please include:

1. Affected version and platform.
2. Steps to reproduce (redact any secrets before pasting output).
3. Impact assessment — what could an attacker do?
4. Suggested fix, if you have one.

We aim to acknowledge reports within 72 hours and publish a fix + advisory
within 14 days for critical issues.

## What we care about most

- **Redaction bypass** — any path where a secret reaches the DB raw, or is
  recoverable after redaction.
- **Web UI exposure** — any way the UI binds beyond `127.0.0.1`, or allows
  unauthenticated state-changing requests from another origin (CSRF).
- **Remote collector abuse** — the agentless SSH collector must stay
  read-only; nothing may execute arbitrary commands on the remote.
- **Data integrity** — any path that lets a non-owner modify or delete
  append-only command/alert rows.
- **Supply chain** — the stdlib-only constraint exists to keep the attack
  surface minimal. A dependency-introducing change is a security concern.

## Security model summary

| Threat | Mitigation |
|--------|-----------|
| Data exfiltration | Zero outbound calls by default; UI binds to 127.0.0.1 only |
| Secret leakage | Redaction at capture + entropy flagging, pre-storage |
| Unauthorized access | DB file perms locked to current user |
| Malicious code execution | Retrace observes, never executes; remote script is read-only |
| Supply chain | Stdlib-only core, no dependency tree |