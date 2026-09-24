<h1 align="center">statusline</h1>

<p align="center">
  <em>Terminal status bars and account tooling for AI coding CLIs —<br>cost, context, rate limits, burn-down, and multi-account routing, at a glance.</em>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/@andrewkent/claude-statusline"><img alt="npm" src="https://img.shields.io/npm/v/@andrewkent/claude-statusline?color=cb3837&logo=npm&label=npm"></a>
  <a href="https://github.com/AndrewTKent/statusline/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/AndrewTKent/statusline/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="platform" src="https://img.shields.io/badge/platform-macOS%20%C2%B7%20Linux-blue">
  <img alt="dependencies" src="https://img.shields.io/badge/deps-jq-lightgrey">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &middot;
  <a href="#claude-code-status-line">Claude Code</a> &middot;
  <a href="#codex-status-line">Codex</a> &middot;
  <a href="#accounts">Accounts</a> &middot;
  <a href="#token-scanning">Token scanning</a> &middot;
  <a href="#agent-metrics">Agent Metrics</a> &middot;
  <a href="#macos-apps">macOS apps</a> &middot;
  <a href="#how-it-works">How it works</a>
</p>

---

Five tools, one repo, shared data files:

- **Claude Code status line** (`bin/statusline.sh`): the multi-line dashboard below.
- **Codex status line** (`bin/codex-statusline`, `codex-top`): the same idea for the Codex CLI.
- **`accounts`** (`bin/accounts.py`): native-profile account routing and a headroom board.
- **Token scanning** (`bin/scan-tokens*`): attributes every token to work/personal and to a payer.
- **Agent Metrics** (`bin/agent-metrics`): opt-in local telemetry and dashboard.

```
model   Fable 5.ultracode
time    ⏱ 2:29:20
account you@example.com
repo    my-project feature/fix-the-thing (v1.2.0*)
pr      #N Fix The Thing The Session Is Working On
context ●●●●●●●○○○○○○○○ 49%
session ●●●●●●●●●○○○○○○ 60.2%   resets 10:00pm PDT
weekly  ●●●●○○○○○○○○○○○ 31.07%  resets jul 27, 12:00pm PDT
fable   ●●●●○○○○○○○○○○○ 33%
usage   today 5.57M · session 1.16M · lifetime 593.31M
  acct        5h   reset   week   fable   reset
· Work       84%   2h15m    51%     80%      2d
· Work-Max   25%   2h25m    68%    100%      2d
* Personal   60%   3h45m    31%     33%      6d
· Side        0%       —   100%     16%      2d
```

The Claude status line is one bash script with no dependency beyond `jq`.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/AndrewTKent/statusline/main/install.sh | bash
```

Restart Claude Code. Requires [`jq`](https://jqlang.github.io/jq/) and a logged-in Claude Code; [`gh`](https://cli.github.com/) is optional, for PR badges.

Other ways in: `npx @andrewkent/claude-statusline install`, or copy `bin/statusline.sh` to `~/.claude/statusline.sh` and point `statusLine` at it ([docs/statusline.md](docs/statusline.md#install)).

## Claude Code status line

Claude Code pipes a JSON status blob to the script, and it prints one labeled row per fact: model, session time, account, repo, branch, PR, context fill, the 5-hour and weekly windows, and token totals. Rows appear only when their data exists. Rate limits refresh in the background, so a render never waits on the network except to validate a changed credential.

Reference: [docs/statusline.md](docs/statusline.md) (every row, PR badges, notifications, files written).

### Formats

Set `FORMAT=` in `~/.claude/statusline.conf` or the `STATUSLINE_FORMAT` env var. Details: [docs/formats.md](docs/formats.md).

| Format | What it renders |
|--------|-----------------|
| `default` | The multi-line dashboard above; falls through to `narrow` below `NARROW_THRESHOLD` (60) columns |
| `compact` | Only the `context` and `session` rows |
| `narrow` | The `default` facts with short labels and bars scaled to the width |
| `sigil` | One dense, width-adaptive line, for tmux status bars |
| `sparkline` | `default` plus cost and 5h-rate trend charts over the last 15 sessions |
| `rprompt` | Writes a zsh right-prompt to `~/.claude/rprompt.txt` |
| `iterm2` | Pushes values to the iTerm2 status bar, or the Kitty window title |

### Configuration

`~/.claude/statusline.conf` is sourced by bash, and every setting is optional. Full list: [docs/configuration.md](docs/configuration.md); annotated example: [`config/statusline.conf.example`](config/statusline.conf.example).

| Key | Effect |
|-----|--------|
| `FORMAT` | Render mode (table above) |
| `DAILY_BUDGET` | Daily cost ceiling; adds the `budget` row |
| `ACCOUNT_LABELS`, `LABEL_COLORS` | Email pattern → short account tag, and its color |
| `SHOW_ACCOUNT_RESETS` | Adds the per-account board |
| `SHARED_ACCOUNT_SNAPSHOT` | Read account and quota rows only from the `accounts` snapshot |
| `STATUSLINE_NOTIFY` | Opt in to macOS threshold notifications |
| `WORK_PATHS`, `PERSONAL_PATHS`, `WORK_KEYWORDS`, `PERSONAL_KEYWORDS` | Work/personal token classifier |

## Codex status line

`codex-statusline` launches Codex with a fixed bottom pane showing the model, elapsed time, routed account, repo, context, tokens, permissions, and each Codex account's weekly quota. `codex-top` is the live view of every parent and subagent session. Both read local state only and call no API. Requires the Codex CLI, Python 3, `~/.local/bin` on `PATH`, and `tmux` for the multi-line footer.

```bash
./install-codex.sh
codex-statusline
```

Reference: [docs/codex.md](docs/codex.md) (scrolling, native mode, permissions default, settings).

## Accounts

`accounts` routes each Claude Code session to one of several subscription accounts, each in its own native profile under `~/.accounts/profiles/<label>`; `codex-accounts` does the same for Codex. A supervisor resumes a session on another account before a window is exhausted. When no safe account is left, the overage guard (on by default) stops it at 100% of a window, before extra usage starts.

```bash
./install-account-router.sh
accounts status
```

| Command | What it does |
|---------|--------------|
| `accounts status` | Mode + per-account 5h/7d/Fable headroom |
| `accounts auto` | Clear global and pane pins, then route supervised sessions to the freshest account |
| `accounts set <label>` | Force every supervised session onto `<label>` |
| `accounts pane set <label>` | Pin only the current terminal pane |
| `accounts fable` | Switch live supervised sessions to Fable while headroom is available |
| `accounts poll` | Refresh dormant stored/native profiles, then poll every routable account |
| `accounts move <label> --to <host>` | Move an account to another machine (`--from <host>` pulls one here) |

Reference: [docs/accounts.md](docs/accounts.md) (all commands, Codex setup, the overage guard, Fable fallback, remote boards, unattended jobs, the handoff notice).

## Token scanning

`bin/scan-tokens.py` scans every session JSONL in the background, attributes each request to work or personal and to the plan that paid, and feeds the `tokens` and `usage` rows. `bin/usage-ledger.py` keeps a per-day, per-model ledger at `~/.claude/usage-ledger.json` that survives transcript cleanup.

Reference: [docs/token-scanning.md](docs/token-scanning.md); engine design in [`bin/ARCHITECTURE.md`](bin/ARCHITECTURE.md).

## Agent Metrics

Opt-in, local-first history and a dashboard for Claude Code and Codex. Neither installer starts it, and nothing is collected until you run one of its commands. It stores numerical metadata and opaque IDs, never prompts, transcript text, credentials or emails, and serves on loopback only. Requires Python 3.11+.

```bash
bin/agent-metrics init
bin/agent-metrics sync --max-lines 5000
bin/agent-metrics serve
```

Reference: [docs/agent-metrics.md](docs/agent-metrics.md).

## macOS apps

A menu bar app, a Raycast extension and a widget bridge read the same data files the status line writes, with no extra API calls.

Reference: [docs/macos.md](docs/macos.md).

## How it works

On each render the script parses the status blob with one `jq` call, resolves the account, updates the daily ledgers, reads the token-scan summary, builds the git/PR segment from caches, and renders. Network refreshes run in the background.

The step-by-step pipeline, cache TTLs and the list of files are in [docs/statusline.md](docs/statusline.md#how-it-works); the token-scan engine, remote boards and unattended jobs are in [`bin/ARCHITECTURE.md`](bin/ARCHITECTURE.md).

## Uninstall

```bash
# curl install
curl -fsSL https://raw.githubusercontent.com/AndrewTKent/statusline/main/uninstall.sh | bash

# npm install
npx @andrewkent/claude-statusline uninstall

# Codex monitor
./uninstall-codex.sh
```

For a manual install, delete `~/.claude/statusline.sh` and remove the `statusLine` key from `~/.claude/settings.json`. For Codex, optionally also `rm ~/.codex/statusline.conf`.

## License

MIT
