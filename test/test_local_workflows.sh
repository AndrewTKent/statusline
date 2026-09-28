#!/usr/bin/env bash
# Workflows this session launched show while they run, read from Claude Code's run journals.
set -euo pipefail
unset ACCOUNTS_ROUTED_LABEL ACCOUNTS_ROUTED_EMAIL ACCOUNTS_ROUTED_ORG_UUID ACCOUNTS_POLICY_SCOPE ACCOUNTS_ROUTER_STATE
unset STATUSLINE_STABLE_HEIGHT STATUSLINE_FORMAT FORMAT CLAUDE_CONFIG_DIR CLAUDE_CODE_OAUTH_TOKEN

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/proj" "$SANDBOX/home/.claude" "$SANDBOX/home/.accounts"
sed "s#/tmp/claude#$SANDBOX/tmp#g" "$ROOT/bin/statusline.sh" > "$SANDBOX/statusline.sh"
chmod +x "$SANDBOX/statusline.sh"
NOW=$(date +%s)
printf '{"version": 1, "generated_at": %s, "health": {"last_success_at": %s, "error": null}, "accounts": {}}\n' \
    "$NOW" "$NOW" > "$SANDBOX/home/.accounts/statusline-snapshot.json"
cat > "$SANDBOX/home/.claude/statusline.conf" <<CONF
SHARED_ACCOUNT_SNAPSHOT=1
SHARED_ACCOUNT_SNAPSHOT_FILE="$SANDBOX/home/.accounts/statusline-snapshot.json"
STATUSLINE_STABLE_HEIGHT=0
MAX_COLS=120
CONF

FAILED=0
fail() { printf 'FAIL: %s\n' "$1" >&2; FAILED=1; }

iso_at() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%S.000Z; }
stamp_at() { date -d "@$1" +%Y%m%d%H%M.%S 2>/dev/null || date -r "$1" +%Y%m%d%H%M.%S; }

# run SESSION RUN_ID [NAME] — a run dir whose journal started three agents, one done and one failed,
# plus, given NAME, the launch result naming it in the session transcript.
run() {
    local session="$1" run_id="$2" name="${3:-}" dir="$SANDBOX/projects/$1/subagents/workflows/$2"
    mkdir -p "$dir" "$SANDBOX/projects/$session/workflows"
    cat > "$dir/journal.jsonl" <<JOURNAL
{"type":"launched"}
{"type":"started","agentId":"a1","label":"one","phase":"P"}
{"type":"started","agentId":"a2","label":"two","phase":"P"}
{"type":"started","agentId":"a3","label":"three","phase":"P"}
{"type":"result","agentId":"a1","result":{"ok":true}}
{"type":"failed","agentId":"a2","error":"boom"}
JOURNAL
    printf '{"agentId":"a3"}\n' > "$dir/agent-a3.jsonl"
    [ -n "$name" ] || return 0
    printf '{"type":"user","timestamp":"%s","toolUseResult":{"status":"async_launched","workflowName":"%s","runId":"%s"}}\n' \
        "$(iso_at $(( NOW - 300 )))" "$name" "$run_id" >> "$SANDBOX/projects/$session.jsonl"
}

# end SESSION RUN_ID — the file Claude Code writes when a run ends, as new as its journal.
end() {
    local journal="$SANDBOX/projects/$1/subagents/workflows/$2/journal.jsonl"
    printf '{"status":"completed"}\n' > "$SANDBOX/projects/$1/workflows/$2.json"
    touch -r "$journal" "$SANDBOX/projects/$1/workflows/$2.json"
}

# age_minutes PATH MINUTES — PATH and everything under it last written MINUTES ago.
age_minutes() {
    find "$1" -exec touch -t "$(stamp_at $(( NOW - $2 * 60 )))" {} +
}

render() {
    printf '{"model": {"display_name": "Claude Test"}, "workspace": {"current_dir": "%s"}, "session_id": "%s", "transcript_path": "%s"}' \
        "$SANDBOX/proj" "$1" "$SANDBOX/projects/$1.jsonl" |
        HOME="$SANDBOX/home" "$SANDBOX/statusline.sh" | sed $'s/\033\\[[0-9;]*m//g'
}

run session wf_running "Lens panel"
run session wf_finished
end session wf_finished
run session wf_dead
age_minutes "$SANDBOX/projects/session/subagents/workflows/wf_dead" 20
run session wf_resumed
end session wf_resumed
age_minutes "$SANDBOX/projects/session/workflows/wf_resumed.json" 5
OUT=$(render session)

grep -qF '·     Lens panel 1/3 agents · 5m · 1 failed' <<< "$OUT" ||
    fail "a running workflow did not show its launch name, agent counts and age: $OUT"
grep -qF 'wf_finished' <<< "$OUT" && fail "a workflow that wrote its end file still showed"
grep -qF 'wf_dead' <<< "$OUT" && fail "a workflow whose run dir went quiet still showed"
grep -qF 'wf_resumed' <<< "$OUT" || fail "a workflow resumed after its end file was written did not show"

# The name comes from the launch result once; later renders never go back to the transcript.
run cached wf_cached "Cached panel"
render cached >/dev/null
sed 's/Cached panel/Renamed panel/' "$SANDBOX/projects/cached.jsonl" > "$SANDBOX/renamed.jsonl"
mv "$SANDBOX/renamed.jsonl" "$SANDBOX/projects/cached.jsonl"
grep -qF 'Renamed panel' <<< "$(render cached)" && fail "a render read the transcript again for a name it had already resolved"

mkdir -p "$SANDBOX/projects/idle/subagents/workflows"
: > "$SANDBOX/projects/idle.jsonl"
grep -qF '· local' <<< "$(render idle)" && fail "the local group rendered with no workflow running"

[ "$FAILED" -eq 0 ] || exit 1
printf 'local workflow tests passed\n'
