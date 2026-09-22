# Codex status line

The same dashboard for the Codex CLI: `codex-statusline` launches Codex with a fixed footer pane, and `codex-top` is the live view of every session.

## Install

**Requires:** Codex CLI &middot; Python 3 &middot; `~/.local/bin` on `PATH` &middot; Multi-line statusline (default): `tmux` &middot; Optional: [`gh`](https://cli.github.com/) for PR linkage

```bash
./install-codex.sh
codex-statusline
codex-statusline --sandbox read-only --ask-for-approval on-request
```

## The footer

The default launcher uses a fixed bottom pane matching the multi-line Claude
Code status view. It shows the current model, elapsed time, routed account,
repository and branch, context use, local tokens consumed, purchased credits,
permissions, and each registered Codex account's weekly quota and reset time.
Codex does not expose a fixed token balance
for subscription limits; the footer reports exact quota percentage remaining
instead of inventing a token estimate. It binds each
footer to the rollout file opened by its owning Codex process, so concurrent and
resumed sessions do not exchange context values. The footer starts at 14 rows
by default and grows to show workflows and account rows, keeping at least ten
rows for the conversation in the standard two-pane layout. It does not shrink
when workflows finish. Local workflows appear below permissions at the bottom. Remote jobs and their
running workflows appear beneath their machine’s account rows. Like Claude,
remote jobs are limited to this session or pane, unowned jobs, and recent results;
set `REMOTE_JOBS_SHOW_ALL=1` to include other sessions’ jobs. If the terminal
is too short for every row, overflow is counted on the last row;
`codex-statusline --footer` shows all rows.

The footer uses the Claude statusline palette: a context range marked in green
and red, percentage-colored limit bars and account cells, and colored usage
totals. Session limits appear when the account publishes a five-hour window;
weekly resets use local dates and times. The account table shows five-hour and
weekly windows, with banked resets only when present. Permissions sit below the
table, and linked checkouts have a separate tree row.

## Scrolling and tmux

Tmux mouse handling is enabled: scroll over the conversation to enter its history,
and press `q` to return to live output. In iTerm2, enable mouse reporting and
wheel reporting, and disable saving alternate-screen lines to scrollback. Otherwise
scrolling can expose stale input bars and blank redraws from the outer terminal.
For ordinary drag-selection and Cmd+C in iTerm2, keep mouse and wheel reporting
enabled but disable reporting of clicks and drags in the profile. Pane
scrollback keeps a 100,000-line history; tune it with
`CODEX_STATUSLINE_HISTORY_LIMIT`. When launched inside an existing
tmux pane, that pane keeps the history depth it was created with; the session
`mouse` and window `history-limit` options are restored when the launcher exits.
When launched outside tmux, detaching (prefix d) leaves Codex running — reattach with
`tmux attach -t codex-statusline-<pid>`; the session ends when Codex exits.

Set `CODEX_STATUSLINE_TMUX_BIN` to use a specific tmux executable or wrapper.
It applies to launcher commands; existing tmux servers keep their running version.

## Native mode

Set `CODEX_STATUSLINE_NATIVE=1` for Codex's compact one-row footer and
`--no-alt-screen`. Native mode preserves normal terminal scrollback but cannot
show account, elapsed time, daily or lifetime tokens, linked agents, or the
Claude Code-style multi-row layout.

## Refresh

The footer refreshes every 3s (`CODEX_STATUSLINE_INTERVAL`) and backs off to a
30s poll once its session has been idle for 10 minutes, exits when the owning
process is gone, and opportunistically truncates the state DB's WAL when it
grows past 128 MB — long-lived footers previously starved SQLite checkpoints
until every Codex query slowed to a crawl.

## Permissions and settings

The launcher defaults to Codex YOLO mode by passing
`--dangerously-bypass-approvals-and-sandbox`. An explicit `-a/--ask-for-approval`,
`-s/--sandbox`, or dangerous-bypass flag replaces that default; profile (`-p`) or
`-c` approval overrides do not. Set
`CODEX_STATUSLINE_MANAGE_APPROVALS=0` to pass no permission default. In multi-line
mode, `tui.status_line=[]` keeps only Codex's compact built-in prompt footer while
the detailed dashboard stays in the fixed pane.
Settings load from `${CODEX_HOME:-~/.codex}/statusline.conf`; non-empty environment
variables override file values, and `CODEX_STATUSLINE_CONFIG` points at a
different file. Annotated example:
[`config/codex-statusline.conf.example`](../config/codex-statusline.conf.example).

## Fleet view and JSON

`codex-top` is the live fleet view for parent and subagent sessions. Both views
read the newest `~/.codex/state_N.sqlite` and rollout JSONL files locally; neither
calls an API. Use `codex-watch --details` for expanded session details or
`codex-statusline --json` for a machine-readable snapshot (renderer-only first flags
dispatch to the renderer; anything else launches Codex). `codex-top` monitors existing sessions.

## Uninstall

```bash
./uninstall-codex.sh
# Optionally: rm ~/.codex/statusline.conf
```

Account routing for Codex (`codex-accounts`) is in [accounts.md](accounts.md).
