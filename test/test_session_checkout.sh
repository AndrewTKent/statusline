#!/usr/bin/env bash
# Regression tests for recent_session_checkout. Pins which checkout the `tree` line names:
# the one worktree a session is working in, and nothing when it is working in several.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home"
mkdir -p "$HOME" "$tmp/cache"

# statusline.sh reads stdin at top level, so extract just the function; its cache path is
# absolute, so point it at the sandbox rather than the host's.
sed -n '/^recent_session_checkout()/,/^}/p' bin/statusline.sh | sed "s#/tmp/claude#$tmp/cache#g" > "$tmp/fns.sh"
grep -q 'recent_session_checkout()' "$tmp/fns.sh" || { echo "FAIL: extraction broke"; exit 1; }
# shellcheck disable=SC1091
source "$tmp/fns.sh"

fail() { echo "FAIL: $1"; exit 1; }

repo="$tmp/project"
mkdir -p "$repo"
git -C "$repo" init -q
printf 'tracked\n' > "$repo/tracked.txt"
git -C "$repo" add tracked.txt
env GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.invalid \
    GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.invalid \
    git -C "$repo" commit -qm initial
git -C "$repo" branch -M main
one="$tmp/worktrees/one"
two="$tmp/worktrees/two"
git -C "$repo" worktree add -q -b feature/one "$one"
git -C "$repo" worktree add -q -b feature/two "$two"

project_dir=$(printf '%s' "$repo" | tr '/' '-')
mkdir -p "$HOME/.claude/projects/$project_dir"

# Each call needs its own session id: the function caches its answer per session.
write_session() {
    local sid="$1"; shift
    local file="$HOME/.claude/projects/$project_dir/${sid}.jsonl" path
    : > "$file"
    for path in "$@"; do
        printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"git -C %s status"}}]}}\n' \
            "$path" >> "$file"
    done
}

# On macOS the sandbox sits under a symlinked /tmp, and git reports the resolved path.
repo_real=$(git -C "$repo" rev-parse --show-toplevel)
one_real=$(git -C "$one" rev-parse --show-toplevel)

write_session one-tree "$one"
got=$(recent_session_checkout one-tree "$repo")
[ "$got" = "$one_real" ] || fail "a session working in one worktree names it, got '$got'"

write_session two-trees "$one" "$two"
got=$(recent_session_checkout two-trees "$repo")
[ "$got" = "$repo_real" ] || fail "a session working in two worktrees names neither, got '$got'"

echo "PASS: test_session_checkout.sh"
