#!/usr/bin/env bash
set -euo pipefail
# A supervised session exports the routed account; the renders below must not inherit it.
unset ACCOUNTS_ROUTED_LABEL ACCOUNTS_ROUTED_EMAIL ACCOUNTS_ROUTED_ORG_UUID ACCOUNTS_POLICY_SCOPE ACCOUNTS_ROUTER_STATE

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
TEST_HOME="$SANDBOX/home"
TEST_TMP="$SANDBOX/tmp/claude"
STUBS="$SANDBOX/stubs"
WORKTREE="$SANDBOX/worktrees/metrics-pane"
mkdir -p "$TEST_HOME/.claude" "$TEST_HOME/.accounts" "$TEST_TMP" "$STUBS"

sed "s#/tmp/claude#$TEST_TMP#g" "$ROOT/bin/statusline.sh" > "$SANDBOX/statusline.sh"
chmod +x "$SANDBOX/statusline.sh"

VIOLATIONS="$SANDBOX/violations"
for command_name in curl claude python3; do
    printf '#!/bin/sh\nprintf "%%s\\n" "$0 $*" >> "$SHARED_TEST_VIOLATIONS"\nexit 97\n' > "$STUBS/$command_name"
    chmod +x "$STUBS/$command_name"
done
printf '#!/bin/sh\nprintf "scanner\\n" >> "$SHARED_TEST_VIOLATIONS"\nexit 97\n' > "$STUBS/scanner.py"
chmod +x "$STUBS/scanner.py"

cat > "$SANDBOX/input.json" <<JSON
{
  "model": {"display_name": "Claude Test"},
  "cost": {"total_cost_usd": 1.25, "total_duration_ms": 42000},
  "context_window": {
    "used_percentage": 37.5,
    "total_input_tokens": 12000,
    "total_output_tokens": 3000,
    "current_usage": {"cache_read_input_tokens": 500, "cache_creation_input_tokens": 100},
    "context_window_size": 200000
  },
  "workspace": {"current_dir": "$SANDBOX/project/nested"},
  "session_id": "session-test",
  "effort": {"level": "high"}
}
JSON
mkdir -p "$SANDBOX/project"
git -C "$SANDBOX/project" init -q
printf 'tracked\n' > "$SANDBOX/project/tracked.txt"
git -C "$SANDBOX/project" add tracked.txt
env GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.invalid \
    GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.invalid \
git -C "$SANDBOX/project" commit -qm initial
git -C "$SANDBOX/project" branch -M main
git -C "$SANDBOX/project" worktree add -q -b feature/agent-metrics-worktree-identity "$WORKTREE"
mkdir -p "$SANDBOX/project/nested"
mkdir -p "$WORKTREE/nested"
project_dir=$(printf '%s' "$SANDBOX/project/nested" | tr '/' '-')
mkdir -p "$TEST_HOME/.claude/projects/$project_dir"
cat > "$TEST_HOME/.claude/projects/$project_dir/session-test.jsonl" <<JSON
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"git -C $WORKTREE/nested status"}}]}}
JSON
sqlite3 "$TEST_HOME/metrics.sqlite3" >/dev/null <<SQL
pragma journal_mode=wal;
create table minute_metrics(provider text, minute integer, input_tokens integer, output_tokens integer);
insert into minute_metrics values('claude', strftime('%s','now','localtime','start of day','utc') * 1000, 1200, 34);
SQL
sqlite3 "$TEST_HOME/metrics.sqlite3" "pragma wal_checkpoint(truncate);" >/dev/null
rm -f "$TEST_HOME/metrics.sqlite3-wal" "$TEST_HOME/metrics.sqlite3-shm"
branch=$(git -C "$WORKTREE" branch --show-current)
worktree_root=$(git -C "$WORKTREE" rev-parse --show-toplevel)
pr_cache_key=$(printf '%s\0%s' "$worktree_root" "$branch" | cksum)
pr_cache_key="${pr_cache_key%% *}"
printf '{"state":"OPEN","number":9,"title":"Cached local PR"}\n' > "$TEST_TMP/statusline-pr-${pr_cache_key}.json"

NOW=$(date +%s)
cat > "$TEST_HOME/.accounts/statusline-snapshot.json" <<JSON
{
  "version": 1,
  "generated_at": ${NOW}.5,
  "health": {"last_success_at": ${NOW}.5, "error": null},
  "mode": {"mode": "auto", "label": null, "global_generation": 7},
  "accounts": {
    "work": {
      "five_hour": {"used_pct": 42.5, "resets_at": "2099-01-01T12:00:00Z", "observed_at": $NOW, "stale": false, "pending_reset": false},
      "seven_day": {"used_pct": 66, "resets_at": "2099-01-07T12:00:00Z", "observed_at": $NOW, "stale": false, "pending_reset": false},
      "scoped": [{"kind": "fable", "label": "Fable", "used_pct": 17, "resets_at": "2099-01-07T12:00:00Z", "observed_at": $NOW, "stale": false, "pending_reset": false}],
      "expired": false,
      "live_leases": 2
    },
    "general": {
      "five_hour": {"used_pct": 12, "resets_at": "2099-01-01T12:00:00Z", "observed_at": $NOW, "stale": false, "pending_reset": false},
      "seven_day": {"used_pct": 24, "resets_at": "2099-01-07T12:00:00Z", "observed_at": $NOW, "stale": false, "pending_reset": false},
      "scoped": [],
      "expired": false,
      "live_leases": 0
    },
    "personal": {
      "five_hour": {"used_pct": 91, "resets_at": null, "observed_at": $NOW, "stale": true, "pending_reset": true},
      "seven_day": {"used_pct": null, "resets_at": null, "observed_at": null, "stale": true, "pending_reset": false},
      "scoped": [],
      "expired": true,
      "live_leases": 0
    }
  }
}
JSON

cat > "$TEST_HOME/.claude/statusline.conf" <<EOF
SHARED_ACCOUNT_SNAPSHOT=1
SHARED_ACCOUNT_SNAPSHOT_FILE="$TEST_HOME/.accounts/statusline-snapshot.json"
SHARED_ACCOUNT_SNAPSHOT_MAX_AGE=180
AGENT_METRICS_RECORDER=1
AGENT_METRICS_DB="$TEST_HOME/metrics.sqlite3"
SHOW_ACCOUNT_RESETS=1
MAX_COLS="\${MAX_COLS:-}"
SCAN_SCRIPT="$STUBS/scanner.py"
EOF

run_statusline() {
    HOME="$TEST_HOME" PATH="$STUBS:$PATH" SHARED_TEST_VIOLATIONS="$VIOLATIONS" \
        ACCOUNTS_ROUTED_LABEL="${1:-work}" ACCOUNTS_POLICY_SCOPE="${2:-global}" \
        "$SANDBOX/statusline.sh" < "$SANDBOX/input.json"
}

snapshot_home_files() {
    find "$TEST_HOME/.claude" "$TEST_HOME/.accounts" -type f -print0 |
        while IFS= read -r -d '' item_file; do
            stat -c '%n:%i:%Y:%s' "$item_file" 2>/dev/null ||
                stat -f '%N:%i:%m:%z' "$item_file"
        done | sort
}

output=$(run_statusline work)
for expected in "Claude Test" "work" "42.5%" "66%" "17%" "fable" "Personal" "pending" "needs reauth" "15.00k" "1.23k" "⌥ metrics-pane" "#9" "Cached local PR"; do
    [[ "$output" == *"$expected"* ]] || { printf 'missing shared value: %s\n' "$expected" >&2; exit 1; }
done
plain_output=$(printf '%s' "$output" | sed $'s/\033\\[[0-9;]*m//g')
[[ "$plain_output" == *$'model   Claude Test · high\n'* ]] || { printf 'model and effort are jammed together\n' >&2; exit 1; }
[[ "$plain_output" == *$'repo    project\ntree    ⌥ metrics-pane\nbranch  feature/agent-metrics-w…\npr      #9 Cached local PR\n'* ]] || {
    printf 'checkout identity is not split into readable rows\n' >&2
    exit 1
}
[[ "$plain_output" != *'project ›'* ]] || { printf 'default renderer collapsed checkout identity\n' >&2; exit 1; }
[[ "$output" == *$'\033]0;metrics-pane'* ]] || { printf 'shared renderer did not refresh terminal title\n' >&2; exit 1; }
[[ "$output" != *"2099-01-01 12:00:00 · stale"* ]] || { printf 'fresh current window rendered stale\n' >&2; exit 1; }
[[ "$output" != *"2099-01-01"* ]] || { printf 'raw reset timestamp leaked into renderer\n' >&2; exit 1; }
[[ "$output" != *$'cost   '* ]] || { printf 'shared renderer added cost noise\n' >&2; exit 1; }
general_line=""
while IFS= read -r output_line; do
    [[ "$output_line" == *General* ]] && general_line="$output_line"
done <<< "$plain_output"
[[ -n "$general_line" && "$general_line" != *"~ stale"* ]] || { printf 'fresh account without a scoped limit rendered stale\n' >&2; exit 1; }
REMOTE_ROOT_DIR="$SANDBOX/remote"
mkdir -p "$REMOTE_ROOT_DIR/devbox"
cat > "$REMOTE_ROOT_DIR/devbox/statusline-snapshot.json" <<JSON
{
  "version": 1,
  "generated_at": $NOW,
  "health": {"last_success_at": $NOW, "error": null},
  "accounts": {
    "team-1": {
      "five_hour": {"used_pct": 8, "resets_at": "2099-01-01T12:00:00Z", "stale": false},
      "seven_day": {"used_pct": 21, "resets_at": "2099-01-07T12:00:00Z", "stale": false},
      "scoped": [{"kind": "fable", "label": "Fable", "used_pct": 3, "resets_at": "2099-01-07T12:00:00Z"}],
      "expired": false
    }
  }
}
JSON
cat > "$REMOTE_ROOT_DIR/devbox/codex-usage.json" <<JSON
{"team-1": {"fetched_at": $NOW, "rate_limits": {"primary": {"resets_at": 4070000000, "used_percent": 37, "window_duration_mins": 10080}, "secondary": null}}}
JSON
write_board_meta() {
    printf '{"name":"devbox","fetched_at":%s,"attempted_at":%s,"error":null,"up":%s}\n' \
        "$1" "$NOW" "$2" > "$REMOTE_ROOT_DIR/devbox/meta.json"
}
render_with_boards() {
    HOME="$TEST_HOME" PATH="$STUBS:$PATH" SHARED_TEST_VIOLATIONS="$VIOLATIONS" \
        ACCOUNTS_ROUTED_LABEL=work ACCOUNTS_POLICY_SCOPE=global \
        REMOTE_BOARDS_DIR="${1:-$REMOTE_ROOT_DIR}" \
        "$SANDBOX/statusline.sh" < "$SANDBOX/input.json" | sed $'s/\033\\[[0-9;]*m//g'
}

write_board_meta "$NOW" true
board_output=$(render_with_boards)
[[ "$board_output" == *$'\n'"· Team-1 "*"8%"* ]] || { printf 'a fresh board did not render its Claude rows\n' >&2; exit 1; }
[[ "$board_output" != *"cx team-1"* ]] || { printf 'a board rendered its Codex row without being asked\n' >&2; exit 1; }
codex_rows_output=$(REMOTE_BOARD_CODEX_ROWS=1 render_with_boards)
[[ "$codex_rows_output" == *"cx team-1"*"37%"* ]] || { printf 'REMOTE_BOARD_CODEX_ROWS=1 did not render the Codex row\n' >&2; exit 1; }
[[ "$board_output" != *$'\u25b8'* ]] || { printf 'a board without a jobs file rendered a job line\n' >&2; exit 1; }
PANE_TEST_ID=$({ printf 'iterm:PANE-TEST' | shasum -a 256 2>/dev/null || printf 'iterm:PANE-TEST' | sha256sum; } | cut -c1-12)
cat > "$REMOTE_ROOT_DIR/devbox/jobs.json" <<JSON
{
  "version": 1,
  "generated_at": $NOW,
  "jobs": {
    "demo-job": {
      "state": "running", "branch": "andrew/demo", "head": "abc1234",
      "account": "team-2", "origin_session": "session-test", "sent_at": $(( NOW - 3600 )),
      "updated_at": $NOW, "handoffs": 2, "report": false,
      "workflows": [
        {"name": "solei loop to sun", "started_at": $(( NOW - 720 )),
         "agents_started": 15, "agents_done": 14, "agents_failed": 0, "running": true},
        {"name": "scoping panel", "started_at": $(( NOW - 7200 )),
         "agents_started": 3, "agents_done": 3, "agents_failed": 0, "running": false}
      ]
    },
    "just-done": {
      "state": "done", "branch": "andrew/done", "head": "def5678",
      "account": "team-2", "origin_session": "", "sent_at": $(( NOW - 7200 )),
      "updated_at": $(( NOW - 120 )), "handoffs": 0, "report": true, "workflows": []
    },
    "their-job": {
      "state": "running", "branch": "andrew/theirs", "head": "1234abc",
      "account": "team-1", "origin_session": "another-session", "sent_at": $(( NOW - 600 )),
      "updated_at": $NOW, "handoffs": 0, "report": false, "workflows": []
    },
    "pane-job": {
      "state": "running", "branch": "andrew/pane", "head": "5678def",
      "account": "team-1", "origin_session": "a-session-this-pane-ran-earlier", "origin_pane": "$PANE_TEST_ID",
      "sent_at": $(( NOW - 600 )), "updated_at": $NOW, "handoffs": 0, "report": false, "workflows": []
    },
    "held-job-on-a-devbox": {
      "state": "held", "held_until": $(( NOW + 3600 )), "branch": "andrew/held", "head": "0fedcba",
      "account": "team-1", "origin_session": "", "sent_at": $(( NOW - 90000 )),
      "updated_at": $(( NOW - 7200 )), "handoffs": 0, "report": false, "workflows": []
    },
    "old-job": {
      "state": "done", "branch": "andrew/old", "head": "9abcdef",
      "account": "team-2", "origin_session": "", "sent_at": $(( NOW - 90000 )),
      "updated_at": $(( NOW - 7200 )), "handoffs": 0, "report": true, "workflows": []
    }
  }
}
JSON
jobs_output=$(render_with_boards)
[[ "$jobs_output" == *"demo-job"*"running"*"team-2"* ]] || { printf 'a running job did not name the account it is on\n' >&2; exit 1; }
[[ "$jobs_output" != *"their-job"* ]] || { printf "another session's job rendered here\n" >&2; exit 1; }
[[ "$jobs_output" != *"this session"* ]] || { printf 'a session showing only its own jobs still marked one as its own\n' >&2; exit 1; }
[[ "$jobs_output" != *"pane-job"* ]] || { printf "another pane's job rendered here\n" >&2; exit 1; }
pane_jobs_output=$(TMUX="" ITERM_SESSION_ID="w0t0p0:PANE-TEST" render_with_boards)
[[ "$pane_jobs_output" == *"pane-job"* ]] || { printf 'a job sent from this pane by an earlier session did not render\n' >&2; exit 1; }
all_jobs_output=$(REMOTE_JOBS_SHOW_ALL=1 render_with_boards)
[[ "$all_jobs_output" == *"their-job"* ]] || { printf 'REMOTE_JOBS_SHOW_ALL=1 did not render every job\n' >&2; exit 1; }
[[ "$(printf '%s\n' "$all_jobs_output" | grep -c 'this session')" = "1" && "$all_jobs_output" == *"demo-job"*"this session"* ]] || { printf 'with every job on screen, only the one this session sent is marked\n' >&2; exit 1; }
[[ "$jobs_output" == *"2 handoffs"* ]] || { printf 'a running job did not count the moves the router made\n' >&2; exit 1; }
[[ "$jobs_output" == *"solei loop to sun"*"14/15 agents"* ]] || { printf "a running job's running workflow did not render\n" >&2; exit 1; }
[[ "$jobs_output" != *"scoping panel"* ]] || { printf 'a workflow that is not running still rendered\n' >&2; exit 1; }
[[ "$jobs_output" == *"just-done"*"done"*"2m ago"* ]] || { printf 'a job that finished recently did not render its state and age\n' >&2; exit 1; }
[[ "$jobs_output" != *"old-job"* ]] || { printf 'a job that finished long ago still rendered\n' >&2; exit 1; }
[[ "$jobs_output" =~ held-job-on-a-devbox\ +held\ ·\ resumes\ [0-9]{1,2}:[0-9]{2}[ap]m\ [A-Z]+ ]] || { printf 'a held job did not say when it resumes\n' >&2; exit 1; }
# 20 characters, the widest name on either board: a held job sizes the column like a running one.
[[ "$jobs_output" =~ demo-job\ {13}running ]] || { printf "a held job's name did not widen the job column\n" >&2; exit 1; }
board_table_line=$(printf '%s\n' "$board_output" | grep -n 'Work' | head -1 | cut -d: -f1)
board_header_line=$(printf '%s\n' "$board_output" | grep -n 'devbox · ' | head -1 | cut -d: -f1)
[ "$board_header_line" -gt "$board_table_line" ] || { printf 'the board block did not render under the local table\n' >&2; exit 1; }

write_board_meta "$(( NOW - 3600 ))" true
stale_board_output=$(render_with_boards)
[[ "$stale_board_output" == *"devbox · 1h ago"* ]] || { printf 'a stale board did not render its age\n' >&2; exit 1; }
[[ "$stale_board_output" == *"· Team-1 "*"8%"* ]] || { printf 'a stale board zeroed its last numbers\n' >&2; exit 1; }

write_board_meta "$NOW" false
stopped_board_output=$(render_with_boards)
[[ "$stopped_board_output" == *"devbox · stopped"* ]] || { printf 'a stopped board did not say so\n' >&2; exit 1; }
[[ "$stopped_board_output" != *"· Team-1 "* ]] || { printf 'a stopped board still rendered rows\n' >&2; exit 1; }
[[ "$stopped_board_output" != *"demo-job"* ]] || { printf 'a stopped board still rendered its jobs\n' >&2; exit 1; }
rm -f "$REMOTE_ROOT_DIR/devbox/jobs.json"

mkdir -p "$SANDBOX/empty-remote"
no_board_output=$(render_with_boards "$SANDBOX/empty-remote")
plain_baseline=$(run_statusline work | sed $'s/\033\\[[0-9;]*m//g')
[ "$no_board_output" = "$plain_baseline" ] || { printf 'no configured board changed the render\n' >&2; exit 1; }

wide_output=$(MAX_COLS=100 COLUMNS=80 run_statusline work)
wide_plain=$(printf '%s' "$wide_output" | sed $'s/\033\\[[0-9;]*m//g')
wide_first_line="${wide_plain%%$'\n'*}"
[[ "${#wide_first_line}" -eq 97 ]] || { printf 'shared renderer did not claim the usable panel width\n' >&2; exit 1; }
wide_prefix="${wide_first_line%%model*}"
[[ -n "$wide_prefix" ]] || { printf 'shared renderer left the status block against the margin\n' >&2; exit 1; }
[[ "$wide_plain" == *$'\n'"${wide_prefix}time"* ]] || { printf 'shared renderer did not center rows as one block\n' >&2; exit 1; }
narrow_output=$(MAX_COLS=20 run_statusline work)
narrow_plain=$(printf '%s' "$narrow_output" | sed $'s/\033\\[[0-9;]*m//g')
narrow_first_line="${narrow_plain%%$'\n'*}"
[[ "$narrow_first_line" == "model   Claude Test · high" ]] || { printf 'narrow shared renderer padded the first row\n' >&2; exit 1; }
compact_output=$(STATUSLINE_FORMAT=sigil run_statusline work)
[[ "$compact_output" == *"project › ⌥ metrics-pane"*"#9"* ]] || {
    printf 'compact renderer hid worktree or PR identity\n' >&2
    exit 1
}
pane_output=$(run_statusline work pane)
[[ "$pane_output" == *"pane pinned"* ]] || { printf 'pane policy scope not shown\n' >&2; exit 1; }
router_state="$TEST_TMP/account-router-parent-$$/state.json"
rm -rf "$(dirname "$router_state")"
mkdir -p "$(dirname "$router_state")"
printf 'global\n' > "${router_state%.json}.policy"
sidecar_output=$(ACCOUNTS_ROUTER_STATE="$router_state" run_statusline work pane)
[[ "$sidecar_output" != *"pane pinned"* ]] || { printf 'router policy sidecar did not override the launch-time scope\n' >&2; exit 1; }
rm -rf "$(dirname "$router_state")"
jq '.session_id = "session-test"' "$SANDBOX/input.json" > "$SANDBOX/router-input.json"
HOME="$TEST_HOME" PATH="$STUBS:$PATH" SHARED_TEST_VIOLATIONS="$VIOLATIONS" \
    ACCOUNTS_ROUTED_LABEL=work ACCOUNTS_POLICY_SCOPE=global \
    ACCOUNTS_ROUTER_STATE="$router_state" \
    "$SANDBOX/statusline.sh" < "$SANDBOX/router-input.json" >/dev/null
jq -e '.session_id == "session-test" and .label == "work"' "$router_state" >/dev/null
rm -rf "$(dirname "$router_state")"
[ ! -s "$VIOLATIONS" ] || { printf 'forbidden command invoked:\n' >&2; tee /dev/stderr < "$VIOLATIONS"; exit 1; }
[ ! -e "$TEST_TMP/statusline-raw.json" ]
[ ! -e "$TEST_TMP/statusline-usage-cache.json" ]
[ ! -e "$TEST_TMP/statusline-profile-cache.json" ]

before=$(snapshot_home_files)
pids=()
for _ in 1 2 3 4 5 6; do
    run_statusline work > "$SANDBOX/concurrent-${_}.out" &
    pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done
after=$(snapshot_home_files)
[ "$before" = "$after" ] || { printf 'shared render wrote under HOME\n' >&2; exit 1; }
[ ! -s "$VIOLATIONS" ]

snapshot_file="$TEST_HOME/.accounts/statusline-snapshot.json"
jq '.health.error = "upstream unavailable" | .accounts.work.five_hour.stale = true' "$snapshot_file" > "$SANDBOX/snapshot.next"
mv "$SANDBOX/snapshot.next" "$snapshot_file"
output=$(run_statusline work)
[[ "$output" == *"42.5%"* ]]
[[ "$output" == *"stale"* ]]
[[ "$output" == *"snapshot error"* ]]
[[ "$output" != *"upstream unavailable"* ]]

output=$(run_statusline absent)
[[ "$output" == *"unknown"* ]]
[[ "$output" == *"stale"* ]]
[[ "$output" != *"session"*" 0%"* ]]

rm "$TEST_HOME/metrics.sqlite3"
printf '{"today":{"total_tokens":9876}}\n' > "$TEST_HOME/.claude/token-scan-summary.json"
output=$(run_statusline work)
[[ "$output" == *"9.88k"* ]] || { printf 'legacy usage fallback was not rendered\n' >&2; exit 1; }

printf '{"version":1,"accounts":{"work":{"five_hour":{},"seven_day":{},"scoped":[]}}}' > "$snapshot_file"
output=$(run_statusline work)
[[ "$output" == *"work"* ]]
[[ "$output" == *"stale"* ]]
[[ "$output" != *"session"*" 0%"* ]]

printf '{not-json' > "$snapshot_file"
output=$(run_statusline work)
[[ "$output" == *"unknown"* ]]
[[ "$output" == *"stale"* ]]

cat > "$TEST_HOME/.claude/statusline.conf" <<EOF
SHOW_ACCOUNT_RESETS=0
MAX_COLS=120
SCAN_SCRIPT="$STUBS/scanner.py"
EOF
unset_output=$(HOME="$TEST_HOME" PATH="$STUBS:$PATH" SHARED_TEST_VIOLATIONS="$VIOLATIONS" \
    "$SANDBOX/statusline.sh" < "$SANDBOX/input.json")
printf '\nSHARED_ACCOUNT_SNAPSHOT=0\n' >> "$TEST_HOME/.claude/statusline.conf"
disabled_output=$(HOME="$TEST_HOME" PATH="$STUBS:$PATH" SHARED_TEST_VIOLATIONS="$VIOLATIONS" \
    "$SANDBOX/statusline.sh" < "$SANDBOX/input.json")
[ "$unset_output" = "$disabled_output" ] || { printf 'disabled mode changed legacy output\n' >&2; exit 1; }

printf 'shared snapshot tests passed\n'
