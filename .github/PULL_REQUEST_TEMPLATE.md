---
name: Pull request
about: Propose a change to Retrace
title: ""
labels: ""
assignees: ""
---

## Summary

<!-- One sentence: what does this PR change and why? -->

## Type of change

- [ ] feat — new user-facing capability
- [ ] fix — bug correction
- [ ] docs — documentation only
- [ ] chore — maintenance, tooling, deps
- [ ] refactor — no behavior change
- [ ] test — test additions or corrections
- [ ] perf — performance improvement
- [ ] security — vulnerability fix or hardening

## Checklist

- [ ] The core stays **stdlib-only** (no new pip dependencies without prior
      discussion in an issue).
- [ ] **Local-first** preserved — no telemetry, no CDN, no network calls
      unless explicitly user-triggered.
- [ ] **Deterministic** — the change does not make capture/detection depend on
      a model.
- [ ] **Redaction-first** — any new data source passes through the redaction
      engine before storage.
- [ ] **Append-only** — no UPDATE/DELETE paths added to the core
      `commands`/`alerts` tables.
- [ ] Tests added or updated for the change (`python3 -m pytest tests/ -v`).
- [ ] Commit history follows [Conventional Commits](https://www.conventionalcommits.org/)
      with allowed types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`,
      `perf`, `security`.
- [ ] No secrets, personal data, or internal hostnames in the diff.
- [ ] README / docs updated if user-facing behavior changed.

## How to test

```bash
# Steps to reproduce / verify the change
```

## Related issues

<!-- Closes #123, refs #456, or N/A -->