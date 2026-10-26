#!/usr/bin/env bash
# Claude Code sends the 5h and 7d windows on stdin (.rate_limits). They must
# render with no token and no network, win over an older polled cache, and a
# failed usage poll (HTTP 429) must back off instead of re-polling every render.
set -uo pipefail
unset ACCOUNTS_ROUTED_LABEL ACCOUNTS_ROUTED_EMAIL ACCOUNTS_ROUTED_ORG_UUID ACCOUNTS_POLICY_SCOPE ACCOUNTS_ROUTER_STATE
unset STATUSLINE_STABLE_HEIGHT STATUSLINE_FORMAT FORMAT CLAUDE_CONFIG_DIR CLAUDE_CODE_OAUTH_TOKEN

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
STUBS="$SANDBOX/stubs"
TMPDIR_SL="$SANDBOX/tmp"
mkdir -p "$STUBS" "$SANDBOX/home/.claude" "$SANDBOX/proj" "$TMPDIR_SL"
sed "s#/tmp/claude#$TMPDIR_SL#g" "$ROOT/bin/statusline.sh" > "$SANDBOX/statusline.sh"
chmod +x "$SANDBOX/statusline.sh"
printf '#!/bin/sh\nexit 1\n' > "$STUBS/security"
# Every request is answered the way a rate-limited endpoint answers, and logged.
cat > "$STUBS/curl" <<STUB
#!/bin/sh
for arg; do last=\$arg; done
echo "\$last" >> "$SANDBOX/curl.log"
printf '{"error":{"type":"rate_limit_error","message":"Rate limited"}}'
STUB
chmod +x "$STUBS/security" "$STUBS/curl"
NOW=$(date +%s)

FAILED=0
fail() { printf 'FAIL: %s\n' "$1" >&2; FAILED=1; }
strip_ansi() { sed $'s/\x1b\\[[0-9;]*m//g'; }
usage_polls() {
    if [ -f "$SANDBOX/curl.log" ]; then grep -c 'oauth/usage' "$SANDBOX/curl.log"; else echo 0; fi
}

# render JSON [VAR=VALUE...] — one status line render, ANSI stripped.
render() {
    local json="$1"
    shift
    printf '%s' "$json" | env HOME="$SANDBOX/home" PATH="$STUBS:$PATH" "$@" \
        bash "$SANDBOX/statusline.sh" 2>/dev/null | strip_ansi
}

# wait_for_refresh — the usage poll runs in a background subshell holding a lock.
wait_for_refresh() {
    local i
    for ((i=0; i<50; i++)); do
        compgen -G "$TMPDIR_SL/statusline-refresh-*.lock" >/dev/null || return 0
        sleep 0.1
    done
}

with_limits=$(printf '{"model":{"display_name":"Opus"},"workspace":{"current_dir":"%s"},"session_id":"s1","rate_limits":{"five_hour":{"used_percentage":37,"resets_at":%s},"seven_day":{"used_percentage":45,"resets_at":%s}}}' \
    "$SANDBOX/proj" "$((NOW + 3600))" "$((NOW + 172800))")
without_limits=$(printf '{"model":{"display_name":"Opus"},"workspace":{"current_dir":"%s"},"session_id":"s2"}' "$SANDBOX/proj")

# 1. No token, no cache: the rows come from stdin alone, reset times included.
out=$(render "$with_limits")
grep -Eq '^session .* 37%.*resets ' <<< "$out" || fail "session row from stdin missing: $out"
grep -Eq '^weekly .* 45%.*resets ' <<< "$out" || fail "weekly row from stdin missing: $out"
[ "$(usage_polls)" = "0" ] || fail "rendered stdin limits should not poll without a token"

# 2. A fresh polled cache with older numbers: stdin wins.
printf '{"five_hour":{"utilization":10,"resets_at":null},"seven_day":{"utilization":20,"resets_at":null}}\n' \
    > "$TMPDIR_SL/statusline-usage-cache-default.json"
out=$(render "$with_limits")
grep -Eq '^session .* 37%' <<< "$out" || fail "stdin should override cached 5h value: $out"
grep -Eq '^weekly .* 45%' <<< "$out" || fail "stdin should override cached 7d value: $out"
rm -f "$TMPDIR_SL/statusline-usage-cache-default.json"

# 3. With a token and no stdin limits, a 429 poll leaves a backoff marker and the
#    next render does not poll again.
render "$without_limits" CLAUDE_CODE_OAUTH_TOKEN=test-token >/dev/null
wait_for_refresh
[ "$(usage_polls)" = "1" ] || fail "first render should poll usage once, polled $(usage_polls)"
[ -f "$TMPDIR_SL/statusline-usage-backoff-default" ] || fail "failed poll should leave a backoff marker"
render "$without_limits" CLAUDE_CODE_OAUTH_TOKEN=test-token >/dev/null
wait_for_refresh
[ "$(usage_polls)" = "1" ] || fail "render inside the backoff window re-polled ($(usage_polls) polls)"

[ "$FAILED" = 0 ] || exit 1
echo "PASS: stdin rate_limits render without network, beat the cache, and a failed poll backs off"
