# Configuration

Create `~/.claude/statusline.conf` (bash, sourced directly). Full annotated version with every knob: [`config/statusline.conf.example`](../config/statusline.conf.example). All settings are optional — the script works with no config file at all.

## Cost and format

| Key | Example | Effect |
|-----|---------|--------|
| `DAILY_BUDGET` | `20` | Daily cost ceiling; enables the `budget` row + 90%/100% notifications (when opted in) |
| `STATUSLINE_NOTIFY` | `1` | Opt in to macOS Notification Center threshold alerts (default off) |
| `FORMAT` | `default` | `default \| compact \| narrow \| sigil \| sparkline \| rprompt \| iterm2`; the `STATUSLINE_FORMAT` env var also sets it. See [formats.md](formats.md) |
| `NARROW_THRESHOLD` | `60` | Terminal width below which `default` falls through to `narrow` |

## Branch display

| Key | Example | Effect |
|-----|---------|--------|
| `BRANCH_PREFIX_STRIP` | `"you/"` | Strip a literal prefix off the displayed branch name |
| `MAX_BRANCH` | `24` | Max visible branch chars before an ellipsis |

## Account labels and routing

| Key | Example | Effect |
|-----|---------|--------|
| `ACCOUNT_LABELS` | `"work:*@company.com personal:me@example.com"` | Email pattern → short tag, first match wins |
| `LABEL_COLORS` | `"work:cyan personal:magenta"` | Tag → color for the `account` row (unmapped tags default to orange) |
| `EMAIL_PAYER_MAP` | `"work:you@company.com personal:me@example.com"` | Which plan paid, for the token scanner's `payer` dimension (independent of the work/personal classifier below) |
| `SHOW_ACCOUNT_RESETS` | `1` | Adds a per-account board (5h%, reset, week%, fable%, reset, work-unit cap) below the main rows |
| `SHARED_ACCOUNT_SNAPSHOT` | `1` | Read account/routing/quota rows only from the private accounts snapshot; use `accounts watch --interval 60` to refresh it explicitly |
| `SHARED_ACCOUNT_SNAPSHOT_FILE`, `SHARED_ACCOUNT_SNAPSHOT_MAX_AGE` | | Override the snapshot path or stale threshold |
| `ACCOUNTS_HARD_SESSION_LIMIT` | `0` | Opt out of proactive routing at a plan wall (100% five-hour, 100% weekly, or 100% Fable for a Fable session); account pins are bypassed only at those boundaries |
| `ACCOUNTS_STRICT_QUOTA` | `1` | Refuse to launch when no account has quota, and terminate at a hard limit when no replacement is available; by default Claude opens and stays open for session history and reset monitoring |
| `ACCOUNTS_HOLD_FOR_RESET` | `1` | When no account can take the work, stop Claude, wait until a window resets and resume it, instead of keeping the interface open (off by default) |

Keys for remote boards, unattended jobs and the handoff notice are described in [accounts.md](accounts.md).

## Token classifier

Feeds the `tokens` row's work/personal split — see `bin/scan-tokens.py` and [token-scanning.md](token-scanning.md).

| Key | Effect |
|-----|--------|
| `WORK_PATHS`, `PERSONAL_PATHS` | Comma-separated cwd/file-path substrings |
| `WORK_KEYWORDS`, `PERSONAL_KEYWORDS` | Comma-separated prompt keywords (weighted 3× a path hit) |

## Bounty and challenge tracker

Opt-in token-goal ETA.

| Key | Effect |
|-----|--------|
| `CHALLENGE_GOAL_M`, `CHALLENGE_LABEL` | Token goal in millions and the label of its row (see the header comment in `bin/statusline.sh`) |
| `CHALLENGE_START`, `BOUNTY_TARGET_TOKENS`, `BOUNTY_LOOKBACK_DAYS`, `BOUNTY_SESSION_GAP_MIN` | Bounty window and target |

## Row visibility

Each defaults on when its data exists; `0` hides it: `SHOW_FABLE_ROW`, `SHOW_TOKENS_ROW`, `SHOW_CHALLENGE_ROW`, `SHOW_BOUNTY_ROW`.

## Live state stack row

`SHOW_BACKENDS_ROW=1` (opt-in) adds a `stack` row from `bin/live-state.py`: a snapshot across Claude (`account-resets.json`), Codex (newest `state_N.sqlite`), and remote autobuild agents (`$AGENT_SESSIONS_PATH`).
