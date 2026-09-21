#!/usr/bin/env bash
#
# Retrace — uninstaller for Linux / macOS / WSL2.
#
# Removes (idempotently):
#   - shell flight-recorder hook (bash/zsh/fish)
#   - PowerShell transcript hook (Windows / WSL)
#   - systemd user units (retrace-agent, retrace-web)
#   - Windows Task Scheduler tasks (via retrace-win.ps1, WSL only)
#   - optionally the database + config (default: KEEP data)
#
# Usage:
#   ./scripts/uninstall.sh            # keep data
#   RETRACE_PURGE=1 ./scripts/uninstall.sh   # also delete DB + config
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

info() { printf '\033[1;36m[retrace]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[retrace]\033[0m ✓ %s\n' "$*"; }
warn() { printf '\033[1;33m[retrace]\033[0m ! %s\n' "$*" >&2; }

# Markers used by retrace/flight_recorder.py + retrace/ps_transcript.py
BASH_MARKER="# --- Retrace flight recorder hook (bash) ---"
PS_MARKER="# --- Retrace flight recorder hook (PowerShell) ---"

# ── 1. Remove bash/zsh/fish hook ──
for RC in ~/.bashrc ~/.zshrc ~/.config/fish/config.fish; do
    if [[ -f "$RC" ]] && grep -qF "$BASH_MARKER" "$RC" 2>/dev/null; then
        cp "$RC" "$RC.retrace-bak"
        # Remove from the marker line through the end of the hook body
        # (the body ends at the line starting with 'trap ' or 'PROMPT_COMMAND').
        awk -v m="$BASH_MARKER" '
            $0 == m { skip=1; next }
            skip && /^trap .*DEBUG/ { skip=0; next }
            skip && /^PROMPT_COMMAND/ { skip=0; next }
            skip { next }
            { print }
        ' "$RC" > "$RC.tmp" && mv "$RC.tmp" "$RC"
        ok "removed hook from $RC (backup: $RC.retrace-bak)"
    fi
done

# ── 2. Remove PowerShell transcript hook ──
PS_PROFILE=""
if [[ -f "/mnt/c/Users/HP/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1" ]]; then
    PS_PROFILE="/mnt/c/Users/HP/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1"
elif [[ -f "$HOME/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1" ]]; then
    PS_PROFILE="$HOME/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1"
fi
if [[ -n "$PS_PROFILE" ]] && grep -qF "$PS_MARKER" "$PS_PROFILE" 2>/dev/null; then
    cp "$PS_PROFILE" "$PS_PROFILE.retrace-bak"
    # Remove from marker line through the end of the block (ends at Start-Transcript line)
    awk -v m="$PS_MARKER" '
        $0 == m { skip=1; next }
        skip && /^Start-Transcript/ { skip=0; next }
        skip { next }
        { print }
    ' "$PS_PROFILE" > "$PS_PROFILE.tmp" && mv "$PS_PROFILE.tmp" "$PS_PROFILE"
    ok "removed PowerShell hook from $PS_PROFILE (backup: $PS_PROFILE.retrace-bak)"
fi

# ── 3. Remove systemd user units ──
if command -v systemctl >/dev/null 2>&1 && systemctl --user list-units >/dev/null 2>&1; then
    for u in retrace-agent.service retrace-web.service; do
        systemctl --user disable --now "$u" 2>/dev/null || true
        rm -f ~/.config/systemd/user/"$u"
        ok "removed systemd unit $u"
    done
    systemctl --user daemon-reload
fi

# ── 4. Remove Windows Task Scheduler tasks (if WSL) ──
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    PS1="$(wslpath -w "$SCRIPT_DIR/retrace-win.ps1" 2>/dev/null || echo "$SCRIPT_DIR/retrace-win.ps1")"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PS1" uninstall 2>/dev/null \
        && ok "removed Windows Task Scheduler tasks" \
        || warn "could not reach Windows Task Scheduler (run scripts\\retrace-win.ps1 uninstall manually)"
fi

# ── 5. Stop any running web/agent processes (best-effort) ──
pkill -f "retrace.cli web" 2>/dev/null || true
pkill -f "retrace.agent" 2>/dev/null || true

# ── 6. Optional purge ──
if [[ "${RETRACE_PURGE:-0}" == "1" ]]; then
    rm -rf ~/.local/share/retrace ~/.config/retrace
    ok "purged database + config"
else
    info "Data kept: ~/.local/share/retrace/ (set RETRACE_PURGE=1 to delete)"
fi

ok "Retrace uninstalled. Reinstall anytime: ./scripts/install.sh"