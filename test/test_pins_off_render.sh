#!/bin/bash
# With ACCOUNTS_PINS=0 the status line shows a stored set, and a launch's
# ACCOUNTS_PIN, the way the router reads them: as auto.
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/.claude" "$sandbox/.accounts" "$sandbox/claude" "$sandbox/proj"
printf '{"version": 2, "mode": "set", "label": "b", "global_generation": 1}\n' >"$sandbox/.accounts/mode.json"

script="$sandbox/statusline.sh"
sed "s#/tmp/claude#$sandbox/claude#g" "$repo/bin/statusline.sh" >"$script"
input="$sandbox/input.json"
sed "s#__CWD__#$sandbox/proj#g" "$here/fixtures/input.json" >"$input"

render() {  # $1: statusline.conf contents, $2: ACCOUNTS_PIN
    printf 'MAX_COLS=200\n%s\n' "$1" >"$sandbox/.claude/statusline.conf"
    (cd "$sandbox/proj" && env -u CLAUDE_CONFIG_DIR -u ACCOUNTS_ROUTER_STATE -u ACCOUNTS_ROUTED_ORG_UUID \
        HOME="$sandbox" TZ=UTC ACCOUNTS_ROUTED_LABEL=a ACCOUNTS_ROUTED_EMAIL=a@example.com ACCOUNTS_PIN="$2" \
        bash "$script" <"$input") 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'
}

fail() { echo "FAIL: $1" >&2; exit 1; }

case "$(render "" "")" in *"set b pending"*) : ;; *) fail "the stored set is not shown with pins on" ;; esac
case "$(render "ACCOUNTS_PINS=0" "")" in *"set b"*) fail "a stored set is shown with pins off" ;; *"· auto"*) : ;; *) fail "pins off does not show auto" ;; esac
case "$(render "" "b")" in *"pin b bypassed"*) : ;; *) fail "the launch pin is not shown with pins on" ;; esac
case "$(render "ACCOUNTS_PINS=0" "b")" in *"pin b"*) fail "a launch pin is shown with pins off" ;; *"· auto"*) : ;; *) fail "pins off with a launch pin does not show auto" ;; esac

echo "OK test_pins_off_render"
