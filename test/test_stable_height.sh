#!/usr/bin/env bash
# Claude Code's inline TUI never reclaims rows a shorter status line vacates, so
# a session's render is padded to the tallest it has been.
set -euo pipefail
unset ACCOUNTS_ROUTED_LABEL ACCOUNTS_ROUTED_EMAIL ACCOUNTS_ROUTED_ORG_UUID ACCOUNTS_POLICY_SCOPE ACCOUNTS_ROUTER_STATE
unset STATUSLINE_STABLE_HEIGHT STATUSLINE_FORMAT FORMAT CLAUDE_CONFIG_DIR CLAUDE_CODE_OAUTH_TOKEN

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
STUBS="$SANDBOX/stubs"
mkdir -p "$STUBS" "$SANDBOX/proj"
sed "s#/tmp/claude#$SANDBOX/tmp#g" "$ROOT/bin/statusline.sh" > "$SANDBOX/statusline.sh"
chmod +x "$SANDBOX/statusline.sh"
# The legacy renderer reaches for the keychain and the usage API; neither belongs in a test.
for command_name in security curl; do
    printf '#!/bin/sh\nexit 1\n' > "$STUBS/$command_name"
    chmod +x "$STUBS/$command_name"
done
NOW=$(date +%s)

FAILED=0
fail() { printf 'FAIL: %s\n' "$1" >&2; FAILED=1; }

# marks HOME — every high-water mark recorded under HOME.
marks() { cat "$1"/.accounts/statusline-height/* 2>/dev/null || true; }

# new_home DIR COUNT — a HOME whose shared snapshot lists COUNT accounts, one row each.
new_home() {
    local home="$1" count="$2" index accounts=""
    mkdir -p "$home/.claude" "$home/.accounts"
    for ((index=1; index<=count; index++)); do
        [ -n "$accounts" ] && accounts+=","
        accounts+="\"acct-$index\": {\"five_hour\": {\"used_pct\": 10, \"stale\": false}, \"seven_day\": {\"used_pct\": 20, \"stale\": false}, \"scoped\": [], \"expired\": false}"
    done
    printf '{"version": 1, "generated_at": %s, "health": {"last_success_at": %s, "error": null}, "accounts": {%s}}\n' \
        "$NOW" "$NOW" "$accounts" > "$home/.accounts/statusline-snapshot.json"
    cat > "$home/.claude/statusline.conf" <<CONF
SHARED_ACCOUNT_SNAPSHOT=1
SHARED_ACCOUNT_SNAPSHOT_FILE="$home/.accounts/statusline-snapshot.json"
SHOW_ACCOUNT_RESETS=1
MAX_COLS=120
CONF
}

# rows HOME SESSION — how many rows the shared renderer emits for SESSION.
rows() {
    printf '{"model": {"display_name": "Claude Test"}, "workspace": {"current_dir": "%s"}, "session_id": "%s"}' \
        "$SANDBOX/proj" "$2" |
        HOME="$1" PATH="$STUBS:$PATH" "$SANDBOX/statusline.sh" | wc -l | tr -d ' '
}

new_home "$SANDBOX/tall-baseline" 3
new_home "$SANDBOX/short-baseline" 1
TALL=$(rows "$SANDBOX/tall-baseline" baseline)
SHORT=$(rows "$SANDBOX/short-baseline" baseline)
[ "$SHORT" -lt "$TALL" ] || { fail "fixture: one account should render shorter than three ($SHORT vs $TALL)"; exit 1; }

# Each claim gets its own HOME, so one claim's marks never satisfy another's.
home="$SANDBOX/shrinking"
new_home "$home" 3
rows "$home" session >/dev/null
new_home "$home" 1
[ "$(rows "$home" session)" -eq "$TALL" ] || fail "a render shorter than the session's mark was not padded to it"

home="$SANDBOX/growing"
new_home "$home" 1
rows "$home" session >/dev/null
new_home "$home" 3
rows "$home" session >/dev/null
[ "$(marks "$home")" = "$TALL" ] || fail "a taller render did not raise the session's mark"

home="$SANDBOX/two-sessions"
new_home "$home" 3
rows "$home" session >/dev/null
new_home "$home" 1
[ "$(rows "$home" another-session)" -eq "$SHORT" ] || fail "another session inherited this session's mark"

home="$SANDBOX/disabled"
new_home "$home" 3
rows "$home" session >/dev/null
new_home "$home" 1
printf 'STATUSLINE_STABLE_HEIGHT=0\n' >> "$home/.claude/statusline.conf"
[ "$(rows "$home" session)" -eq "$SHORT" ] || fail "STATUSLINE_STABLE_HEIGHT=0 still padded the render"

home="$SANDBOX/legacy"
mkdir -p "$home/.claude"
printf 'MAX_COLS=120\n' > "$home/.claude/statusline.conf"
printf '{"model": {"display_name": "Claude Test"}, "workspace": {"current_dir": "%s"}, "session_id": "session"}' "$SANDBOX/proj" |
    (cd "$SANDBOX/proj" && HOME="$home" PATH="$STUBS:$PATH" "$SANDBOX/statusline.sh") >/dev/null
[ -n "$(marks "$home")" ] || fail "the legacy renderer kept no height mark for its session"

[ "$FAILED" -eq 0 ] || exit 1
printf 'stable height tests passed\n'
