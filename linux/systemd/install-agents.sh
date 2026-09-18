#!/usr/bin/env bash
# install-agents.sh — install the background pollers as systemd user timers.
#
# The Linux counterpart of macos/launchd/install-agents.sh. A machine that
# skipped this step reads as working: its account board just goes stale, and
# anything pulling that board sees numbers that stopped moving.
#
# Usage:
#     linux/systemd/install-agents.sh           # install/reload all
#     linux/systemd/install-agents.sh --remove  # disable and delete all
set -uo pipefail

UNIT_SRC="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
ACTION="${1:-install}"
if [ -r "$HOME/.claude/statusline.conf" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$HOME/.claude/statusline.conf"
    set +a
fi
INTERVAL="${ACCOUNTS_POLL_INTERVAL:-120}"
case "$INTERVAL" in
    ''|*[!0-9]*|0) INTERVAL=120 ;;
esac
JOBS_INTERVAL="${JOBS_PUBLISH_INTERVAL:-30}"
case "$JOBS_INTERVAL" in
    ''|*[!0-9]*|0) JOBS_INTERVAL=30 ;;
esac

if [ -n "${STATUSLINE_SKIP_AGENTS:-}" ]; then
    echo "STATUSLINE_SKIP_AGENTS set — skipping systemd timers"
    exit 0
fi
if [ "$(uname)" != "Linux" ]; then
    echo "systemd timers are Linux-only — skipping"
    exit 0
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl unavailable — skipping"
    exit 0
fi

failed=0

remove_unit() {
    local name="$1"
    systemctl --user disable --now "$name.timer" >/dev/null 2>&1 || true
    rm -f "$UNIT_DIR/$name.service" "$UNIT_DIR/$name.timer"
    echo "Removed $name"
}

install_unit() {
    local name="$1" launcher="$2" suffix
    if [ ! -x "$HOME/.local/bin/$launcher" ]; then
        echo "[!] $launcher launcher missing or not executable — skipping $name" >&2
        failed=1
        return
    fi
    for suffix in service timer; do
        sed -e "s#__HOME__#$HOME#g" -e "s#__INTERVAL__#$INTERVAL#g" \
            -e "s#__JOBS_INTERVAL__#$JOBS_INTERVAL#g" \
            "$UNIT_SRC/$name.$suffix.template" > "$UNIT_DIR/$name.$suffix" || {
            echo "[!] could not write $name.$suffix" >&2
            failed=1
            return
        }
    done
    systemctl --user enable --now "$name.timer" || failed=1
}

mkdir -p "$UNIT_DIR"
case "$ACTION" in
    --remove|remove)
        remove_unit claude-accounts-poll
        remove_unit claude-codex-accounts-poll
        remove_unit claude-jobs-publish
        systemctl --user daemon-reload || true
        exit 0
        ;;
esac

systemctl --user daemon-reload || failed=1
install_unit claude-accounts-poll accounts
install_unit claude-codex-accounts-poll codex-accounts
install_unit claude-jobs-publish jobs-publish

echo "Installed systemd user timers (boards every ${INTERVAL}s, jobs every ${JOBS_INTERVAL}s)"
echo "  units:  $UNIT_DIR/claude-{,codex-}accounts-poll.{service,timer}"
echo "          $UNIT_DIR/claude-jobs-publish.{service,timer}"
echo "  status: systemctl --user list-timers"
# Without linger a user timer stops at logout, which is exactly when an
# unattended box needs to keep publishing its board.
if command -v loginctl >/dev/null 2>&1 &&
    [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "  note:   run 'loginctl enable-linger $USER' so the timers survive logout"
fi

exit "$failed"
