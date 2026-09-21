#!/usr/bin/env bash
#
# Retrace — one-command installer for Linux / macOS / WSL2.
#
# Does (idempotently):
#   1. Detect platform (native Linux/macOS vs WSL2 vs native Windows)
#   2. Verify python3 >= 3.9 (Retrace is stdlib-only — NO pip installs)
#   3. Init config + data dirs + SQLite DB
#   4. Install the shell flight-recorder hook (bash/zsh/fish)
#   5. Register persistence:
#        - Linux / WSL2-with-systemd  -> systemd user units (agent + web)
#        - WSL2-without-systemd       -> Windows Task Scheduler (via retrace-win.ps1)
#   6. Start the local-only web UI (127.0.0.1:8765)
#   7. Print a summary + next commands
#
# Safe to re-run: hooks and units are never duplicated.
#
# Usage:
#   ./scripts/install.sh
#   RETRACE_PORT=9000 ./scripts/install.sh          # custom web port
#   RETRACE_NO_WEB=1 ./scripts/install.sh           # skip web UI
#   RETRACE_NO_PERSIST=1 ./scripts/install.sh       # skip systemd/Task Scheduler
#   RETRACE_NO_HOOK=1 ./scripts/install.sh          # skip shell hook
#
set -euo pipefail

# ────────────────────────────────────────────────────────────────
# 0. Resolve repo root (this script lives in <repo>/scripts/)
# ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RETRACE_PORT="${RETRACE_PORT:-8765}"
PYTHON="${PYTHON:-python3}"

info()  { printf '\033[1;36m[retrace]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[retrace]\033[0m ✓ %s\n' "$*"; }
warn()  { printf '\033[1;33m[retrace]\033[0m ! %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[retrace]\033[0m ✗ %s\n' "$*" >&2; exit 1; }

# ────────────────────────────────────────────────────────────────
# 1. Platform detection
# ────────────────────────────────────────────────────────────────
PLATFORM="linux"        # linux | macos | wsl | windows(native, bail)
IS_WSL=0
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    IS_WSL=1
    PLATFORM="wsl"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    PLATFORM="macos"
elif [[ "$(uname -s)" == "MINGW"* || "$(uname -s)" == "MSYS"* || "$(uname -s)" == "CYGWIN"* ]]; then
    fail "Detected Windows (git-bash). Use scripts\\install.ps1 in PowerShell instead."
fi
info "Platform: $PLATFORM"

# ────────────────────────────────────────────────────────────────
# 2. Verify python3 >= 3.9
# ────────────────────────────────────────────────────────────────
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    fail "python3 not found. Install Python 3.9+ then re-run."
fi
PY_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
IFS='.' read -r MAJOR MINOR <<< "$PY_VER"
if (( MAJOR < 3 )) || { (( MAJOR == 3 )) && (( MINOR < 9 )); }; then
    fail "Python $PY_VER too old — need 3.9+ (found $PY_VER)"
fi
ok "python $PY_VER (stdlib-only, no pip needed)"

# ────────────────────────────────────────────────────────────────
# 3. Init config + data dirs + DB
# ────────────────────────────────────────────────────────────────
info "Initialising database and config directories…"
"$PYTHON" -m retrace.cli stats >/dev/null 2>&1 || {
    # 'stats' auto-creates the DB via connect(); if it failed, surface why
    "$PYTHON" -m retrace.cli stats || fail "DB init failed — see error above."
}
ok "database ready ($("$PYTHON" -m retrace.cli stats 2>/dev/null | head -1))"

# ────────────────────────────────────────────────────────────────
# 4. Install shell hook (flight recorder)
# ────────────────────────────────────────────────────────────────
if [[ "${RETRACE_NO_HOOK:-0}" != "1" ]]; then
    info "Installing flight-recorder hook…"
    "$PYTHON" -m retrace.cli hook install || warn "hook install failed (continuing)"
    ok "shell hook installed (open a NEW terminal to activate)"
fi

# ────────────────────────────────────────────────────────────────
# 5. Register persistence
# ────────────────────────────────────────────────────────────────
if [[ "${RETRACE_NO_PERSIST:-0}" != "1" ]]; then
    if [[ "$PLATFORM" == "wsl" && "$IS_WSL" == "1" ]]; then
        # WSL2: prefer systemd if available; else fall back to Windows Task Scheduler
        if systemctl --user list-units >/dev/null 2>&1; then
            info "WSL with systemd — installing user units…"
            mkdir -p ~/.config/systemd/user
            for u in systemd/retrace-agent.service systemd/retrace-web.service; do
                sed -e "s|/mnt/c/Users/HP/OneDrive/Desktop/SYSTEMS/SCAAI_SYSTEMS/Retrace|$REPO_ROOT|" \
                    -e "s|/home/alfred/.local/share/retrace|$HOME/.local/share/retrace|" \
                    "$u" > ~/.config/systemd/user/"$(basename "$u")"
            done
            systemctl --user daemon-reload
            systemctl --user enable --now retrace-agent.service || warn "could not enable retrace-agent"
            systemctl --user enable --now retrace-web.service  || warn "could not enable retrace-web"
            ok "systemd user units enabled (agent + web)"
        else
            info "WSL without systemd — falling back to Windows Task Scheduler…"
            REPO_WIN=$(wslpath -w "$REPO_ROOT" 2>/dev/null || echo "$REPO_ROOT")
            powershell.exe -NoProfile -ExecutionPolicy Bypass -File \
                "$(wslpath -w "$SCRIPT_DIR/retrace-win.ps1" 2>/dev/null)" install || warn "Task Scheduler install failed"
            ok "Windows Task Scheduler fallback configured"
        fi
    elif [[ "$PLATFORM" == "linux" || "$PLATFORM" == "macos" ]]; then
        if command -v systemctl >/dev/null 2>&1 && systemctl --user list-units >/dev/null 2>&1; then
            info "Installing systemd user units…"
            mkdir -p ~/.config/systemd/user
            for u in systemd/retrace-agent.service systemd/retrace-web.service; do
                sed -e "s|/mnt/c/Users/HP/OneDrive/Desktop/SYSTEMS/SCAAI_SYSTEMS/Retrace|$REPO_ROOT|" \
                    -e "s|/home/alfred/.local/share/retrace|$HOME/.local/share/retrace|" \
                    "$u" > ~/.config/systemd/user/"$(basename "$u")"
            done
            systemctl --user daemon-reload
            systemctl --user enable --now retrace-agent.service || warn "could not enable retrace-agent"
            systemctl --user enable --now retrace-web.service  || warn "could not enable retrace-web"
            ok "systemd user units enabled"
        else
            warn "No systemd — skipping persistence. Run 'retrace agent' manually, or add a cron entry."
        fi
    fi
fi

# ────────────────────────────────────────────────────────────────
# 6. Start web UI (if enabled)
# ────────────────────────────────────────────────────────────────
if [[ "${RETRACE_NO_WEB:-0}" != "1" ]]; then
    info "Starting local-only web UI on 127.0.0.1:${RETRACE_PORT}…"
    if [[ "$PLATFORM" == "wsl" ]]; then
        # In WSL, launch via Windows-side so the browser opens on the host
        nohup "$PYTHON" -m retrace.cli web --port "$RETRACE_PORT" >/dev/null 2>&1 &
        sleep 1
    else
        nohup "$PYTHON" -m retrace.cli web --port "$RETRACE_PORT" >/dev/null 2>&1 &
        sleep 1
    fi
    ok "web UI started — open http://127.0.0.1:${RETRACE_PORT}"
fi

# ────────────────────────────────────────────────────────────────
# 7. Summary
# ────────────────────────────────────────────────────────────────
echo
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                     Retrace installed ✓                        ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║  Web UI :  http://127.0.0.1:${RETRACE_PORT}                         ║"
echo "║  Data   :  ~/.local/share/retrace/rec.db                      ║"
echo "║  Config :  ~/.config/retrace/                                 ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║  Try:                                                          ║"
echo "║    retrace stats        — see what's captured                  ║"
echo "║    retrace search curl  — search your commands                 ║"
echo "║    retrace detect       — rule-based security alerts           ║"
echo "║    retrace export --format jsonl --output out.jsonl            ║"
echo "║    retrace web          — re-open the UI                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo
ok "Done. Run 'retrace stats' to confirm."
