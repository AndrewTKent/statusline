# Accounts

`accounts` (`bin/accounts.py`) and `codex-accounts` (`bin/codex_accounts.py`)
provide per-session routing and headroom boards. Each Claude account gets a
native config under `~/.accounts/profiles/<label>`; each Codex account gets an
isolated `CODEX_HOME` under `~/.codex-accounts/profiles/<label>`.
Credentials and entitlement caches are isolated; projects, transcripts, settings,
skills, and plugins are shared. Interactive sessions remain first-party
subscription sessions instead of API-token sessions.

For shared statusline rendering, enable `SHARED_ACCOUNT_SNAPSHOT=1` in
`statusline.conf`. The router installer registers a launch agent that runs
`accounts poll` every minute; `accounts watch --interval 60` is the foreground
alternative.

## Install

Install the router from a local checkout:

```bash
./install-account-router.sh
```

The installer puts the Claude router wrapper at `~/.local/bin/claude`, keeps
native Claude binaries under `~/.local/share/claude/versions`, installs both
router toolsets under `~/.local/bin`, and prepends supervised launchers from
`~/.accounts/bin` and `~/.codex-accounts/bin` in new zsh sessions.

## Quota limits

Hard-limit routing is on by default. A supervised session resumes on another
safe account on the next supervisor check after its account reaches 100% of
the five-hour or weekly window. When none is available, Claude stays open so
you can read the session and monitor reset times. The router keeps checking
for a replacement account. `ACCOUNTS_STRICT_QUOTA=1` opts into terminating
instead. A Fable session is also moved at 100% Fable utilization, falling back
to Opus on the
same account when its general windows still have headroom. That fallback
happens inside the running process, whether the session launched on Fable or
switched to it later (`ACCOUNTS_FABLE_FALLBACK_MODEL` changes the model), and
the session returns to Fable on its own once the window resets; only a move to
another account restarts it. Claude's quota enforcement and extra-usage settings
still govern model calls; leaving the interface open does not change them.
`ACCOUNTS_HARD_SESSION_LIMIT=0` turns off proactive hard-limit routing.

`ACCOUNTS_HOLD_FOR_RESET=1` changes what happens at a hard limit when no
other account can take the session. With the hold on, the router stops the
child, then sleeps until the soonest reset on the board plus two minutes, polls, and tries to route
again, re-holding if there is still no room. No sleep runs longer than 15
minutes, which is also how often it rechecks a board that names no reset at all:
once a five-hour reset slips into the past on a row the poll has not advanced,
the soonest moment left on the board is the weekly reset, and sleeping to that
would park the session for days. Within a sleep it waits in 30-second slices
against the wall clock, so a suspended machine wakes on time. The stderr line
says when it will resume, in local time. Ctrl-C ends a hold and exits as it does
today. The hold also applies before the first launch, so a session started with
every account walled waits instead of opening on an exhausted one.

A resumed session carries a first message saying it was held, from when to when
and why, whatever `ACCOUNTS_HANDOFF_NOTICE` is set to — a held session that comes
back silently is the failure the hold exists to fix. As with a move, a session
with no transcript to resume gets its original prompt again instead, because
nothing was in flight to report. Each hold counts as one handoff in the move
count the status line shows.

`ACCOUNTS_HARD_SESSION_LIMIT` and `ACCOUNTS_STRICT_QUOTA` are listed in [configuration.md](configuration.md#account-labels-and-routing).

## Commands

| Command | What it does |
|---------|---------------|
| `accounts set <label>` | Force every supervised session onto `<label>` |
| `accounts pane set <label>` | Pin only the current terminal pane to `<label>` |
| `accounts pane clear` | Return the current pane to the global policy |
| `accounts auto` | Clear global and pane pins, then route supervised sessions to the freshest account |
| `accounts fable` | Switch live supervised sessions to Fable while headroom is available |
| `accounts status` | Mode + per-account 5h/7d/Fable headroom + ⚠login flags |
| `accounts poll` | Refresh dormant stored/native profiles, then poll every routable account |
| `accounts refresh [label]` | Refresh stale file-backed credentials without a browser |
| `accounts mint <label>` | Mint + vault a 1-year token for headless jobs |
| `accounts tokens` | List minted tokens and expiry |
| `accounts sync` | Converge the token vault with a second machine |
| `accounts pick-env` | Emit `CLAUDE_CONFIG_DIR` and account metadata |

Codex uses its own command because the two CLIs expose different auth and quota
interfaces:

| Command | What it does |
|---------|---------------|
| `codex-accounts register [label]` | Register the current authenticated Codex home |
| `codex-accounts login [label]` | Authenticate another ChatGPT account in an isolated home |
| `codex-accounts auto` | Route supervised sessions to the account with the most headroom |
| `codex-accounts set <label>` | Pin supervised sessions to one account |
| `codex-accounts status` | Show the mode and each account's quota windows |
| `codex-accounts poll` | Refresh quota through Codex app-server without an inference |
| `codex-accounts pick --poll` | Poll and print the account selected by the current policy |

Codex requires `cli_auth_credentials_store = "file"` and this SessionStart hook:

```toml
[[hooks.SessionStart]]
command = "if [ -x \"$HOME/.local/bin/codex-account-session\" ]; then exec \"$HOME/.local/bin/codex-account-session\"; fi"
```

The hook binds each thread to its routed label so the statusline stays correct
when concurrent sessions use different accounts. Automatic handoffs require a
fresh weekly usage reading strictly above 80% and another account whose binding
usage is at least 15 points lower. Poll errors, missing or stale weekly readings,
and short-window usage alone do not trigger a handoff. A hard `codex-accounts set`
pin moves the thread on the next supervisor check.

Inside Claude Code, prefix these with `!` (for example,
`!accounts set acme-max`). Set `"respondToBashCommands": false` in
`~/.claude/settings.json` so the switch does not trigger an LLM response.

## The router

`claude-router.py` supervises interactive sessions. It reserves the selected
account, watches the active model's quota windows, and resumes the exact session
under another isolated profile before a window is exhausted. The shell never
regains control during a handoff. Changing to Fable mode also moves running
supervised sessions to Fable in place — except a session you explicitly put on
another model (a `--model` launch flag or a live `/model` switch), which stays
there until you switch back to `/model fable` or re-run `accounts fable`. A
live switch to the fallback model itself (Opus by default) is read as the
fallback, not a pin: the session already retries Fable each turn. If
every Fable-capable account is gated, the same session resumes on Opus using the
safest general-model account. Chasing Fable never moves a session onto an
account already at the departure wall, which would hand it straight back. A Fable session that exhausts its window on the
account it is on falls back to Opus inside the running process and returns to
Fable on its own when the window resets; only a move to another account
restarts the session.
Minted long-lived tokens remain outside `~/.claude`
(`~/.accounts/vault.json`); archival copies only session JSONLs from
`~/.claude/projects`.

## Remote account boards

One machine's board can appear under another's. The remote machine runs this
same router over its own accounts and polls itself; this machine copies the two
files that poll publishes and renders them below its local table.

```
  acct                5h   reset   week   fable   reset
* Work               84%   2h15m    51%     80%      2d
· Personal            0%       —   100%      8%     23h
· devbox · 1m ago
· Team-1             12%   3h40m    26%      4%      5d
· ▸ fix-cache        running · Team-1 · 2 handoffs
·     review loop 14/15 agents · 12m
```

On the machine being watched, install the router and let its poller run:

```bash
./install-account-router.sh          # picks launchd on macOS, systemd on Linux
loginctl enable-linger "$USER"       # Linux: keep the timers up after logout
```

On the machine doing the watching, name the boards in `~/.claude/statusline.conf`:

```bash
REMOTE_ACCOUNT_BOARDS="devbox:devbox"                        # <name>:<ssh-host>
REMOTE_BOARD_UP_DEVBOX='"$HOME/.local/bin/devbox-cli" status | grep -q running'
```

Mechanics, and the reason they are worth knowing:

- **`accounts poll` does the pulling, not the renderer.** It fetches
  `~/.accounts/statusline-snapshot.json` and `~/.codex-accounts/usage.json` over
  SSH (`BatchMode`, short `ConnectTimeout`, hard overall timeout) into
  `~/.accounts/remote/<name>/`. Every renderer reads only those local copies, so
  no render ever blocks on the network.
- **Credentials never cross.** Only percentages, reset times, labels and plan
  names are in those two files, and token-shaped keys are dropped on arrival.
- **Freshness is stated, not implied.** Numbers younger than
  `REMOTE_BOARD_MAX_AGE` (default 900s) render as current; older or failed pulls
  keep the last numbers, dimmed, with their age. Nothing is ever shown as zero
  because a pull failed.
- **A stopped machine is left alone.** When a board's up-check exits non-zero it
  is not contacted at all, and the row reads `<name> · stopped`. The check runs
  as `bash -lc` under the poller's environment, so give it absolute paths.
- **Codex rides along, in its own place.** The same pull carries the remote
  Codex quota rows, which appear in this machine's Codex statusline and
  `codex-top`. `REMOTE_BOARD_CODEX_ROWS=1` also lists them, as `cx <label>`,
  under the board's Claude rows.

## Unattended jobs on a board

A board that runs unattended Claude Code sessions publishes them too, and they
render under its account rows: one line per job, and under a running job one
line per workflow that still has agents out.

On the board, each job is a tmux session working out of `~/handoffs/<slug>/`.
The `jobs-publish` timer installed with the router rewrites `~/handoffs/jobs.json`
every 30 seconds, and `accounts poll` here copies it alongside the two board
files.

- **State is observed, not reported.** A job is `running` while both its tmux
  session and the router supervising it are alive, `held` while that router is
  waiting for a window to reset (with the time it resumes), `done` or `blocked`
  once it writes a `report.md` (`status: blocked` on the first line means
  blocked), and `gone` otherwise. A wedged session cannot claim to be healthy.
- **A session outliving its router reads as `gone`, not `running`.** The router
  writes `/tmp/claude/account-router-<pid>.json` from launch, and that file
  outlives the process, so liveness is the pid in its name. A tmux session
  sitting at a bare shell after its router exited used to publish as `running`
  forever. A job that names no worktree cannot be matched to a router at all, so
  there the tmux session is still the whole test.
- **Workflow progress comes from Claude Code's own journals.** A workflow counts
  as running when agents it started have no result yet and the job is still
  running. `14/15 agents` is agents finished over agents started.
- **A board with a running job is pulled every 30 seconds** instead of
  `REMOTE_BOARD_PULL_INTERVAL`, and drops back when the last job stops.
- **A job shows where it was sent from.** `job.json` may carry `origin_session`
  (the sender's Claude Code session id) and `origin_pane` (the first 12 hex of
  the SHA-256 of `tmux:<server>:<pane>`, `iterm:<session uuid>` or
  `term:<TERM_SESSION_ID>`). A job renders only in a status line whose session
  or terminal pane matches, so it follows the pane across `/clear` and new
  sessions and stays off every other pane. A job with neither shows everywhere.
  `REMOTE_JOBS_SHOW_ALL=1` lists every job and marks this pane's own with
  `← this session`.
- **Recently finished jobs stay visible for half an hour**, showing their state
  and age, then drop off. A `held` job stays on screen however long the hold
  lasts — it has not finished — and reads `held · resumes 10:20pm PDT` in the
  reader's own time zone. `REMOTE_JOB_NAME_MAX` (default 20) caps the name so a
  long branch cannot widen the table.

## Being told the session was moved

A cross-account move stops the running process and relaunches it on `--resume`.
Claude Code adds "Continue from where you left off." only when the transcript
ends mid-turn, so a session that was idle between turns never learns it moved —
and any in-process workflow, subagent or background task died with the old
process. `ACCOUNTS_HANDOFF_NOTICE=1` makes the relaunch carry a first message
naming the accounts, the reason, and what was lost, so an unattended session can
restart what was in flight. Off by default; a session with no transcript to
resume never gets one. The prompt a session was launched with stays behind on a
relaunch that resumes — it is already in the transcript, and a second prompt
beside the notice makes the CLI submit neither.

