#!/usr/bin/env bash
# A lane's status line: the PRs and tickets the ownership list gives it, and the photon lanes it owns.
set -euo pipefail
unset ACCOUNTS_ROUTED_LABEL ACCOUNTS_ROUTED_EMAIL ACCOUNTS_ROUTED_ORG_UUID ACCOUNTS_POLICY_SCOPE ACCOUNTS_ROUTER_STATE
unset TMUX TMUX_PANE ITERM_SESSION_ID TERM_SESSION_ID REMOTE_JOBS_SHOW_ALL

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
TEST_HOME="$SANDBOX/home"
TEST_TMP="$SANDBOX/tmp/claude"
mkdir -p "$TEST_HOME/.claude" "$TEST_HOME/.accounts/remote/photon" "$TEST_TMP"
sed "s#/tmp/claude#$TEST_TMP#g" "$ROOT/bin/statusline.sh" > "$SANDBOX/statusline.sh"
export STATUSLINE_NOW=2000000000
NOW=$STATUSLINE_NOW

# Every check runs, so one break shows every check it fails.
FAILURES=0
fail() { printf 'FAIL: %s\n%s\n' "$1" "${2:-}" >&2; FAILURES=$(( FAILURES + 1 )); }

# Every laptop lane sits on main in one shared checkout.
CHECKOUT="$SANDBOX/app"
git init -q -b main "$CHECKOUT"
git -C "$CHECKOUT" remote add origin git@github.com:acme/app.git
env GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@example.invalid \
    git -C "$CHECKOUT" commit -q --allow-empty -m initial

TRANSCRIPT="$SANDBOX/session.jsonl"
name_session() { printf '{"type":"custom-title","customTitle":"%s","sessionId":"s1"}\n' "$1" >> "$TRANSCRIPT"; }
printf '{"type":"user","message":{"content":"hello"}}\n' > "$TRANSCRIPT"

cat > "$SANDBOX/input.json" <<JSON
{"model": {"display_name": "Claude Test"}, "cost": {"total_cost_usd": 0, "total_duration_ms": 0},
 "context_window": {"used_percentage": 10, "total_input_tokens": 1, "total_output_tokens": 1},
 "workspace": {"current_dir": "$CHECKOUT"}, "session_id": "s1", "transcript_path": "$TRANSCRIPT"}
JSON
cat > "$TEST_HOME/.accounts/statusline-snapshot.json" <<JSON
{"version": 1, "generated_at": $NOW, "health": {"last_success_at": $NOW, "error": null},
 "accounts": {"work": {"five_hour": {"used_pct": 10, "resets_at": null, "stale": false},
                       "seven_day": {"used_pct": 20, "resets_at": null, "stale": false}, "scoped": []}}}
JSON
cp "$TEST_HOME/.accounts/statusline-snapshot.json" "$TEST_HOME/.accounts/remote/photon/statusline-snapshot.json"
printf '{"name":"photon","fetched_at":%s,"error":null,"up":true}\n' "$NOW" > "$TEST_HOME/.accounts/remote/photon/meta.json"
cat > "$TEST_HOME/.claude/statusline.conf" <<EOF
SHARED_ACCOUNT_SNAPSHOT=1
SHOW_ACCOUNT_RESETS=1
MAX_COLS=60
EOF

# The render path reads local files only: any of these called records itself.
STUBS="$SANDBOX/stubs"
mkdir -p "$STUBS"
for command_name in gh curl ssh python3; do
    printf '#!/bin/sh\necho "$0 $*" >> "%s/calls"\nexit 97\n' "$SANDBOX" > "$STUBS/$command_name"
    chmod +x "$STUBS/$command_name"
done

render() {
    HOME="$TEST_HOME" PATH="$STUBS:$PATH" ACCOUNTS_ROUTED_LABEL=work STATUSLINE_STABLE_HEIGHT=0 \
        bash "$SANDBOX/statusline.sh" < "$SANDBOX/input.json" | sed $'s/\033\\[[0-9;]*m//g; s/\033\\][^\a]*\a//g'
}
home_files() { find "$TEST_HOME" -type f -exec cksum {} + | sort; }

# Synthetic ids: each PR is acme/app#<number> unless it names another repository.
TICKET_INFRA="ABC-1"
TICKET_AGENT="ABC-2"
nums() { local n out=""; for n in "$@"; do out+="${out:+ }#$n"; done; printf '%s' "$out"; }
cat > "$TEST_HOME/.accounts/lanes.json" <<JSON
{"version": 1, "lanes": {
  "infra": {"prs": ["acme/app#101", "acme/app#102", "acme/app#103", "acme/app#104", "acme/app#105",
                    "acme/app#106", "acme/app#107", "acme/app#108", "acme/tools#7", "acme/app#109",
                    "acme/app#111", "acme/app#112", "acme/app#113"],
            "tickets": ["$TICKET_INFRA"], "photon": ["gate-watch"]},
  "agent": {"prs": ["acme/app#110"], "tickets": ["$TICKET_AGENT"], "photon": ["slug-to-agent"]}}}
JSON
cat > "$TEST_HOME/.accounts/lane-prs.json" <<JSON
{"version": 1, "checked_at": $(( NOW - 60 )), "error": null, "prs": {
  "acme/app#101": {"state": "OPEN", "status": "open"},
  "acme/app#102": {"state": "OPEN", "status": "failing"},
  "acme/app#103": {"state": "OPEN", "status": "open"},
  "acme/app#104": {"state": "OPEN", "status": "open"},
  "acme/app#105": {"state": "OPEN", "status": "open"},
  "acme/app#106": {"state": "OPEN", "status": "open"},
  "acme/app#107": {"state": "OPEN", "status": "open"},
  "acme/app#108": {"state": "OPEN", "status": "open"},
  "acme/tools#7": {"state": "OPEN", "status": "open"},
  "acme/app#111": {"state": "OPEN", "status": "conflicts"},
  "acme/app#112": {"state": "MISSING", "status": "missing"},
  "acme/app#113": {"state": "OPEN", "status": "a-state-this-version-does-not-know"},
  "acme/app#110": {"state": "OPEN", "status": "approved"}}}
JSON

# A session named for a lane, on main, shows the lane, its PRs grouped by state and its tickets.
name_session infra
before=$(home_files)
infra=$(render)
[ "$(home_files)" = "$before" ] && [ ! -e "$SANDBOX/calls" ] || fail 'a lane render wrote under HOME or reached the network' "$(cat "$SANDBOX/calls" 2>/dev/null)"
[[ "$infra" == *"lane    infra · 13 PRs · $TICKET_INFRA"* ]] || fail 'a lane on main did not name itself, its PR count and its ticket' "$infra"
# The renderer centers the block; its rows are read from the lane row's own margin.
block=$(awk '/^ *lane /{match($0, /^ */); pad = RLENGTH; on = 1; next} on && /^ {8}/{print substr($0, pad + 1); next} on{exit}' <<< "$infra")
expected="        checks failing $(nums 102)
        conflicts      $(nums 111)
        open           $(nums 101 103 104 105 106 107 108)
                       tools#7
        not found      $(nums 112)
        not read yet   $(nums 109 113)"
[ "$block" = "$expected" ] || fail "a lane's PRs are not grouped most pressing first, named for their repo outside this one, and wrapped to the width" "$block"

# States the poll has not refreshed in ten minutes say how old they are.
jq '.checked_at = '"$(( NOW - 1500 ))" "$TEST_HOME/.accounts/lane-prs.json" > "$SANDBOX/states" && cp "$SANDBOX/states" "$TEST_HOME/.accounts/lane-prs.json"
[[ "$(render)" == *"· states 25m old"* ]] || fail 'states the poll stopped refreshing did not say how old they are' "$(render)"

# A PR read that has never succeeded says so.
printf '{"version": 1, "checked_at": 0, "attempted_at": %s, "error": "GitHub read failed", "prs": {}}\n' "$NOW" > "$SANDBOX/never-read.json"
cp "$TEST_HOME/.accounts/lane-prs.json" "$SANDBOX/states"
cp "$SANDBOX/never-read.json" "$TEST_HOME/.accounts/lane-prs.json"
[[ "$(render)" == *"· states never read: GitHub read failed"* ]] || fail 'a PR read that never succeeded did not say so' "$(render)"
cp "$SANDBOX/states" "$TEST_HOME/.accounts/lane-prs.json"

# A rename is read on the next render, even one the render caught half-written.
printf '{"ty' >> "$TRANSCRIPT"
render > /dev/null
printf 'pe":"custom-title","customTitle":"Agent","sessionId":"s1"}\n' >> "$TRANSCRIPT"
[[ "$(render)" == *"lane    agent "* ]] || fail 'a rename read while half-written was lost' "$(render)"

# Photon lanes, seen from this session (s1) as the agent lane.
name_session agent
OLD=$(( NOW - 7200 ))
job() {  # slug state origin_session origin_lane pr_url
    local pr=""
    [ -n "${5:-}" ] && pr=", \"pr\": {\"number\": 1, \"url\": \"$5\"}"
    printf '"%s": {"state": "%s", "updated_at": %s, "origin_session": "%s", "origin_pane": "", "origin_lane": "%s"%s}' \
        "$1" "$2" "$OLD" "$3" "$4" "$pr"
}
cat > "$TEST_HOME/.accounts/remote/photon/jobs.json" <<JSON
{"version": 1, "generated_at": $NOW, "jobs": {
  $(job slug-to-agent running an-earlier-pane ""),
  $(job pr-to-infra running s1 agent https://github.com/Acme/App/pull/102),
  $(job sent-by-agent running an-earlier-pane agent),
  $(job sent-by-infra running "" infra),
  $(job blocked-lane blocked "" ""),
  $(job gate-watch running "" "")
}}
JSON
jq -e '.jobs | length == 6' "$TEST_HOME/.accounts/remote/photon/jobs.json" >/dev/null || fail 'the jobs fixture does not parse'
agent=$(render)
[[ "$agent" == *"slug-to-agent"* ]] || fail "a photon lane the list gives this lane by slug did not show" "$agent"
[[ "$agent" != *"pr-to-infra"* ]] || fail "a photon lane whose PR the list gives another lane still showed in the session that sent it" "$agent"
[[ "$agent" == *"sent-by-agent"* ]] || fail "a photon lane an earlier session of this lane sent did not show" "$agent"
[[ "$agent" != *"sent-by-infra"* ]] || fail "a photon lane only another lane sent showed here" "$agent"
[[ "$agent" =~ blocked-lane\ +blocked ]] || fail "a blocked photon lane aged off the status line" "$agent"
[[ "$agent" != *"gate-watch"* ]] || fail "a photon lane with no sender that the list gives another lane showed here" "$agent"

# One lane written wrong takes nothing from the others, and list text is printed as text.
jq '.lanes.infra.prs = "acme/app#101" | .lanes.agent.tickets += ["AB\\cX-3"]' "$TEST_HOME/.accounts/lanes.json" > "$SANDBOX/lanes" &&
    cp "$TEST_HOME/.accounts/lanes.json" "$SANDBOX/lanes.good" && cp "$SANDBOX/lanes" "$TEST_HOME/.accounts/lanes.json"
printf '{"prs": ' > "$TEST_HOME/.accounts/lane-prs.json"
damaged=$(render)
{ [[ "$damaged" == *"lane    agent · 1 PR · $TICKET_AGENT ABcX-3"* ]] && [[ "$damaged" == *"not read yet $(nums 110)"* ]]; } || fail "a wrong field in another lane, or a broken states file, took this lane's rows or claims" "$damaged"
printf '{"lanes": ' > "$TEST_HOME/.accounts/lanes.json"
[[ "$(render)" == *"lane    agent · lanes.json is not valid"* ]] || fail 'a list that does not parse did not say so' "$(render)"
cp "$SANDBOX/lanes.good" "$TEST_HOME/.accounts/lanes.json"
cp "$SANDBOX/states" "$TEST_HOME/.accounts/lane-prs.json"
[[ "$(REMOTE_JOBS_SHOW_ALL=1 render | grep 'sent-by-agent')" == *"this session"* ]] ||
    fail "with every photon lane on screen, one this lane sent is not marked as this session's" "$(REMOTE_JOBS_SHOW_ALL=1 render)"

# A transcript that starts over is read from its start.
printf '{"type":"custom-title","customTitle":"infra"}\n' > "$TRANSCRIPT"
[[ "$(render)" == *"lane    infra "* ]] || fail 'a transcript that shrank kept the name it had before' "$(render)"

[ "$FAILURES" -eq 0 ] || exit 1
printf 'lane ownership render tests passed\n'
