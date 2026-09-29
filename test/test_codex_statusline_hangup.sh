#!/usr/bin/env bash
# shellcheck disable=SC2329
# codex-statusline runs Codex in a tmux session of its own. A deliberate detach keeps
# it running; a lost terminal must end it, or the orphan holds the thread open.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
export TMUX_TMPDIR="$SANDBOX/tmux"
mkdir -p "$TMUX_TMPDIR" "$SANDBOX/home/.codex"
cleanup() {
    tmux kill-server 2>/dev/null || true
    tmux -L outer kill-server 2>/dev/null || true
    rm -rf "$SANDBOX"
}
trap cleanup EXIT
unset TMUX

FAKE_CODEX="$SANDBOX/codex"
printf '#!/bin/sh\nexec sleep 300\n' > "$FAKE_CODEX"
chmod +x "$FAKE_CODEX"

FAILED=0
fail() { printf 'FAIL: %s\n' "$1" >&2; FAILED=1; }

# until_true SECONDS COMMAND... — polls COMMAND every 0.2s; false if it never succeeds.
until_true() {
    local deadline=$(( $(date +%s) + $1 )); shift
    until "$@"; do
        (( $(date +%s) < deadline )) || return 1
        sleep 0.2
    done
}
inner_session() { tmux ls -F '#{session_name}' 2>/dev/null | grep '^codex-statusline-' || true; }
has_inner() { [ -n "$(inner_session)" ]; }
no_inner() { [ -z "$(inner_session)" ]; }
outer_gone() { ! tmux -L outer has-session -t =wrapper 2>/dev/null; }

# The wrapper gets a real terminal from an outer tmux server; killing that server hangs it up.
start_wrapper() {
    tmux -L outer new-session -d -s wrapper -x 120 -y 40 \
        "env -u TMUX HOME=$SANDBOX/home TMUX_TMPDIR=$TMUX_TMPDIR CODEX_STATUSLINE_CODEX_BIN=$FAKE_CODEX bash $ROOT/bin/codex-statusline"
    until_true 10 has_inner || fail "wrapper never started its Codex session"
}

start_wrapper
tmux -L outer kill-server
until_true 10 no_inner || fail "a hung-up terminal left the Codex session running"

start_wrapper
tmux detach-client -s "=$(inner_session)"
until_true 10 outer_gone || fail "the wrapper did not exit after a detach"
has_inner || fail "a deliberate detach ended the Codex session"

[ "$FAILED" = 0 ] && echo "codex-statusline hangup: ok"
exit "$FAILED"
