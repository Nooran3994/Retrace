#!/usr/bin/env bash
# verify-sync.sh — authoritative local↔remote sync check for Retrace.
#
# PURPOSE
#   End the class of error where a remote state is *claimed* without a fresh
#   check. This script is the single source of truth: it fetches from origin
#   and compares SHAs. If it has not run, a sync claim must not be made.
#
# USAGE
#   scripts/verify-sync.sh            # human-readable verdict
#   scripts/verify-sync.sh --json     # machine-readable verdict (for CI/agents)
#   scripts/verify-sync.sh --strict   # exit 1 if any divergence (for hooks)
#
# EXIT CODES
#   0  IN_SYNC        local HEAD == origin/<branch>
#   1  DIVERGED       local and remote differ (use --strict to fail)
#   2  NO_REMOTE      no remote configured or fetch failed
#   3  UNCOMMITTED    working tree has uncommitted changes

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

MODE="human"
for arg in "$@"; do
  case "$arg" in
    --json)  MODE="json" ;;
    --strict) MODE="strict" ;;
  esac
done

# ── 1. Fetch authoritative remote state ──────────────────────────────────────
if ! git remote get-url origin >/dev/null 2>&1; then
  if [ "$MODE" = "json" ]; then
    echo '{"status":"NO_REMOTE","local":null,"remote":null,"branch":null,"uncommitted":null}'
  else
    echo "NO_REMOTE: no 'origin' remote configured in $REPO_DIR"
  fi
  exit 2
fi

if ! git fetch origin --quiet 2>/dev/null; then
  if [ "$MODE" = "json" ]; then
    echo '{"status":"FETCH_FAILED","local":null,"remote":null,"branch":null,"uncommitted":null}'
  else
    echo "FETCH_FAILED: could not reach origin — do not claim sync state"
  fi
  exit 2
fi

# ── 2. Gather facts ──────────────────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo "MISSING")"
UNCOMMITTED="$(git status --porcelain | wc -l | tr -d ' ')"

# ── 3. Verdict ───────────────────────────────────────────────────────────────
STATUS="IN_SYNC"
EXIT_CODE=0
if [ "$REMOTE_SHA" = "MISSING" ]; then
  STATUS="NO_REMOTE_BRANCH"
  EXIT_CODE=1
elif [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  STATUS="DIVERGED"
  EXIT_CODE=1
elif [ "$UNCOMMITTED" != "0" ]; then
  STATUS="UNCOMMITTED"
  EXIT_CODE=3
fi

# ── 4. Output ────────────────────────────────────────────────────────────────
if [ "$MODE" = "json" ]; then
  printf '{"status":"%s","local":"%s","remote":"%s","branch":"%s","uncommitted":"%s"}\n' \
    "$STATUS" "$LOCAL_SHA" "$REMOTE_SHA" "$BRANCH" "$UNCOMMITTED"
else
  echo "branch:      $BRANCH"
  echo "local HEAD:  $LOCAL_SHA"
  echo "remote HEAD: $REMOTE_SHA"
  echo "uncommitted: $UNCOMMITTED file(s)"
  case "$STATUS" in
    IN_SYNC)        echo "verdict:     IN_SYNC — local matches origin/${BRANCH}" ;;
    DIVERGED)       echo "verdict:     DIVERGED — local ≠ remote. Run: git push origin ${BRANCH}" ;;
    NO_REMOTE_BRANCH) echo "verdict:     NO_REMOTE_BRANCH — origin/${BRANCH} does not exist yet" ;;
    UNCOMMITTED)    echo "verdict:     UNCOMMITTED — ${UNCOMMITTED} file(s) not committed" ;;
  esac
fi

exit "$EXIT_CODE"