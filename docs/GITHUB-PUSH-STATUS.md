# GitHub Push Status — Retrace

## Current State (2026-09-21)

- **Token authenticates**: `**REDACTED**` → `GET /user` returns 200.
- **Token is persisted** in `~/.scaai/mcp_servers.json` (`GITHUB_PERSONAL_ACCESS_TOKEN` env for the GitHub MCP server) — any future session loads it automatically.
- **API reports** `permissions: {admin, maintain, push, triage, pull}` but that reflects the **repo owner's** rights, NOT the token's write scope.
- **`git push origin main` FAILS** with:
  `remote: Permission to Nooran3994/Retrace.git denied to Nooran3994.` (403)
- **Root cause**: fine-grained PAT has `Contents: Read-only`. Git push requires `Contents: Read and write`.

## The One Manual Step (only Elan can do this)

1. Go to https://github.com/settings/tokens?type=beta
2. Select the token starting `**token (redacted)**`
3. Repository access → ensure `Nooran3994/Retrace` is selected
4. Permissions → Repository permissions → **Contents** → change **Read-only** → **Read and write**
5. Save (token value does NOT change when you edit permissions)

## After the Fix

```bash
cd /mnt/c/Users/HP/OneDrive/Desktop/SYSTEMS/SCAAI_SYSTEMS/Retrace
git push origin main
# 10 commits:
# 1e941e1 README → dd4fe03 installer → c749d9d Win persistence → b690d7a agent/systemd
# 1e92ed1 remote agent → b63493c web UI/watch → f740d33 detectors → 85e9620 Phase 2
# 798fcf7 flight fix → 31fc37c Phase 1
```

## How to Re-verify

```bash
# 1. Token authenticates?
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" https://api.github.com/user
# → expect 200

# 2. Push works?
git push origin main
# → expect no error, "main -> main" line
```

## Why This Keeps Happening

The GitHub API endpoint `GET /repos/{owner}/{repo}` returns the *owner's* permissions envelope, not the *token's* scopes. So `push: true` in the JSON is misleading for a fine-grained token whose Contents permission is read-only. **Always test with an actual `git push`**, never trust the API permissions object.