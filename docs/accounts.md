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
| `accounts set <label>` | Force every supervised session onto `<label>`; refused where `ACCOUNTS_PINS=0` |
| `accounts pane set <label>` | Pin only the current terminal pane to `<label>`; refused where `ACCOUNTS_PINS=0` |
| `accounts pane clear` | Return the current pane to the global policy |
| `accounts auto` | Clear global and pane pins, then route supervised sessions to the freshest account |
| `accounts fable` | Switch live supervised sessions to Fable while headroom is available |
| `accounts status` | Mode, the route order, per-account 5h/7d/Fable headroom + ⚠login flags |
| `accounts lead` | One line naming where new work on this machine goes now, Claude or Codex: `claude <label>`, `codex <label>` or `none` |
| `accounts schedule` | List the board schedule's windows and what the board shows now |
| `accounts schedule add <days> [HH:MM-HH:MM] [--show all\|<label,...>]` | Add a window: days such as `sat,sun` or `mon-fri`, hours in the board's zone (all day when omitted), and the local accounts it shows (`all` by default). The first window holding the current day and time decides the board; outside every window `ACCOUNTS_HIDE_OTHERS` and `ACCOUNTS_HIDE` apply |
| `accounts schedule remove <n>` / `accounts schedule clear` | Remove one window, or all of them |
| `accounts poll` | Refresh dormant stored/native profiles, then poll every routable account |
| `accounts refresh [label]` | Refresh stale file-backed credentials without a browser |
| `accounts mint <label>` | Mint + vault a 1-year token for headless jobs |
| `accounts tokens` | List minted tokens and expiry |
| `accounts sync` | Converge the token vault with a second machine |
| `accounts move <label> --to <host>` | Move an account to `<host>` over ssh (`--from <host>` pulls one here): import on the destination, then forget on the source; a failed import leaves the source untouched. Sessions live on the account move off it on their own |
| `accounts login --box <host> <label>` | Log a box in to `<label>` on its own: a fresh `claude auth login` runs in a tmux session inside the box's profile, the URL prints here, the code you paste here goes to the box over stdin, and the box's `accounts register` records the login as the account it resolves to. No credential leaves either machine. A bare `--box` (`accounts login <label> --box`) uses `ACCOUNTS_BOX`, else the first `ACCOUNTS_SYNC_HOSTS` entry. Needs a terminal |
| `accounts register <label>` | Record the login already in `<label>`'s profile as the account it resolves to, refusing an account another label holds; `login --box` runs it on the box |
| `accounts export <label>` / `import` / `forget <label>` | The pieces `move` is built from: `export` writes the account as JSON to a pipe (never a terminal), `import` installs it from stdin, `forget` removes it and keeps the profile directory. `forget` refuses the pinned account; live sessions on it reroute first (up to 60s), and if any stay the account is put back. `export` and `import` refuse to run outside `move`: a copied login races the original over refresh-token rotation and signs one machine out, so a second machine logs in on its own with `login --box` |
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

## Which accounts take work first

`ACCOUNTS_ROUTE_ORDER` in `~/.claude/statusline.conf` names the accounts the
router spends first, as label patterns, the group routed first leading. It is
read on this machine only, at every launch and handoff. Unset, every account is
in one group and the router sends work to the account with the most headroom, as
it always has.

Andrew, 2026-11-16 3:28pm PST: "max accounts should be burned down first",
"before team accounts". A machine with both kinds sets:

```bash
ACCOUNTS_ROUTE_ORDER="acme-max*"
```

- **Groups.** A label joins the group of the first pattern it matches
  (shell-style `*`, `?` and `[...]`, case-sensitive). A label no pattern matches
  goes in a last group, after them all.
- **Only an account that can take the work is ranked by its group:** under 80%
  of its five-hour window and under 90% of its week, the same ceilings a row
  must clear to receive work without the order. A plan with no five-hour limit,
  such as Max, counts as 0% there, so only its week limits it. An account past
  either ceiling goes in the last group, so a nearly spent account is never
  chosen for its group alone.
- **A new session** goes to the first group with an account that can take the
  work. Within a group, most headroom first.
- **A handoff** off an account at its 90% departure wall goes to the first group
  whose best account can take the work and buys the 15-point handoff margin. A
  group whose best account can't falls through to the next.
- **No account under those ceilings:** the last resort takes the most runway
  under the 97% hard wall, as it does without the order.
- **Fable.** A Fable session goes to the first group with Fable headroom, lowest
  Fable use first within it, and Fable mode's preference ranks the same way.
- **A running session is never moved because an earlier group has room.** It
  moves only at its departure wall, its Fable wall or a pin, as before. Once the
  first group is spent and work spills to the next, sessions there stay until
  their own wall; new sessions go back to the first group as soon as one of its
  accounts can take work again, such as after its weekly reset.
- **A pin outranks the order** (`accounts set`, `accounts pane set`,
  `ACCOUNTS_PIN`).
- `accounts status` prints the order in force, for example
  `order: acme-max* → rest, most headroom first within each`, and lists the
  accounts in that order. The status line's `✓ best next` marker follows it
  too.

## Spending accounts evenly over their week

`ACCOUNTS_PACED` in `~/.claude/statusline.conf` names accounts this machine
spends a day's share of at a time, as label patterns, so their weekly allowance
is used evenly and used up by each reset instead of burned early or left to
expire. The patterns name Claude accounts and, by the same labels, the
machine's Codex accounts (`codex-accounts status`). Unset, nothing is paced and
routing is unchanged.

Andrew, 2026-11-18 2:47pm PST: "we should burn through each of those accounts
enough such that we use an squivalent each day", "after we burn an amount - out
of the total token usage for all those accounts - be it on one or two etc of
those three acounts", "we switch to max", "or dont allow them to be used"; and
2:52pm PST: "the weekly amount could be all drained from one account that day".
2026-11-23 1:02pm PST: "make sure that it is drawing from the two claude team
and the codex team"; 1:14pm PST: "we need codex in there too", "it needs to be
used".

```bash
ACCOUNTS_PACED="acme-team-*"
```

- **One budget a day for the paced accounts together.** The unit is points of
  weekly allowance: one point is 1% of an account's week, read from its
  `seven_day` usage, or for a Codex account from the usage window that spans a
  week (10080 minutes, `primary` or `secondary` depending on the plan), so the
  pool assumes the paced accounts have plans of the same size. A Codex account
  counts apart from a Claude account of the same label. Each account brings
  what it has left, spread evenly over the time to its weekly reset, today's
  part of it: `(100 − used) × length of today ÷ time to reset`, at most all it
  has left, which is what an account that resets during the day brings. The
  pool is the sum.
- **A Codex week not yet started** reports a reset a full week out on every
  poll. Its share is taken from the day's first reading, `100 × time left today
  ÷ 7 days`, about 14.3 points for a whole day, and later polls that push the
  reset out do not change it.
- **The budget is the pool's, not each account's.** There is no daily cap per
  account: one account may burn the whole pool, all the way to its weekly
  ceiling, and the router then moves to the next paced account.
- **While the pool has budget left, the paced accounts take work first**, ahead
  of `ACCOUNTS_ROUTE_ORDER`, among those that can take it: a Claude account
  under the 80% five-hour and 90% weekly ceilings, a Codex account the Codex
  router would route to. First in line is an account whose week ends today,
  until it has burned its share, since what it leaves is lost at the reset;
  then the one furthest behind its own share of today, the lowest burn ÷ share,
  so an account the day has not used yet, a Codex one included, takes the work
  next. Ties, and an account with no share, go by soonest weekly reset. A Claude
  launch picks among the Claude accounts; `accounts lead` names the one
  account, Claude or Codex, a launcher that can run either should use. A
  running session moves only at its own wall, as before.
- **Once the pool's burn reaches its budget the paced accounts take no work
  until the next local midnight.** New sessions and handoffs go to the
  machine's other accounts, and `accounts lead` names a Codex account only if it
  is not paced. A session running on a paced account moves at its
  next handoff point, after any Workflow in flight finishes, however long that
  takes. With no other account, the router does what it does when every account
  is walled: it refuses a launch under `ACCOUNTS_STRICT_QUOTA=1` and stops a
  running session once no Workflow is in flight, holds it under
  `ACCOUNTS_HOLD_FOR_RESET=1` until midnight, and otherwise leaves it open.
- **A day starts at local midnight** in the zone the status line shows times
  in (`STATUSLINE_TZ`, `~/.claude/statusline-tz`, `TZ`, else Pacific), never a
  server's UTC clock.
- **A weekly reset during the day** adds the fresh week's share for the rest of
  the day, counted from the reset; what the old week burned before it still
  counts.
- **Burn** is the rise in each account's weekly use since the day's first
  reading, at every reading: each `accounts poll` and each router check. Work
  done between two readings shows at the next one, so the cut-off can run past
  the budget by that much. A day that runs over leaves less for the days after,
  and their budgets shrink to match.
- **The day's first reading is kept** in `~/.accounts/pacing.json`, so a
  restart keeps the count. Pacing turned on, or the ledger lost, mid-day budgets
  only the rest of that day. An unreadable ledger is kept aside as
  `pacing.json.corrupt.<time>` and the day counts from then.
- **Bad data never blocks an account.** A paced account that can take work but
  has a stale weekly figure (no good poll for 3 hours, Claude or Codex; a failed
  Codex poll keeps the last good figure beside its error) or no weekly reset
  leaves the machine routing as if unpaced until it reads again, and
  `accounts status` says so. An account that needs a login, or is excluded, is
  left out of the pool.
- **A pin outranks pacing**, as it outranks the order.
- `accounts status` prints the day's standing, for example
  `paced: acme-team-* — first (a week ending today, then the furthest behind its share); today's budget 19.5 points, 3.2 burned, 16.3 left`,
  or, once it is spent,
  `paced: acme-team-* — today's budget of 19.5 points is spent (20.1 burned); they take no work until Thu 12:00am PST`.
  Each account in the pool is flagged `[paced]`, or `[paced, out for today]`
  once the budget is spent, and a paced Codex account is listed below the
  Claude ones as the board names it, `acme-team-1-codex`.
  The board's snapshot carries it, so the account board, here and on a machine
  that shows this one as a remote board, ends that board's header line with it:
  `■ devbox · 12s ago · team 9.0/15.2 today`, then
  `team spent Nov 23`, the day it was spent, once it is spent, or
  `not pacing: <why>` on bad data. The paced set reads by a short name, its
  patterns without a trailing `-*` or a leading `sol-` (`sol-team-*` reads
  `team`), and as written when they do not come to one name. A local table with
  no `LOCAL_MACHINE_NAME` header carries it on the column header above its
  rows. The `✓ best next` marker on the older account table, without
  `SHARED_ACCOUNT_SNAPSHOT=1`, does not follow pacing.
- `accounts lead` prints `claude <label>` or `codex <label>` for the paced
  account first in line while the pool has budget; otherwise `claude <label>`
  for the account `accounts status` names under `general:` if it is short of
  100% on both windows; otherwise `codex <label>` for the account the Codex
  router would pick, leaving out a paced-out one; otherwise `none`. With
  `ACCOUNTS_PACED` unset that is: Claude when it has room, else Codex when it
  has room.

## Naming this machine

`LOCAL_MACHINE_NAME` labels the local table with a bold header line, the same
way a remote board is labeled by its own name below it — purely cosmetic, so a
board with several machines on it reads as "whose numbers are these" at a
glance instead of by position alone.

```bash
LOCAL_MACHINE_NAME="laptop"
```

```
  acct                5h   reset   week   fable   reset
■ laptop
* Work               84%   2h15m    51%     80%      2d
· Personal            0%       —   100%      8%     23h
■ devbox · 1m ago
```

Unset by default — no header, the prior rendering.

## Remote account boards

One machine's board can appear under another's. The remote machine runs this
same router over its own accounts and polls itself; this machine copies the two
files that poll publishes and renders them below its local table.

```
  acct                5h   reset   week   fable   reset
■ laptop
* Work               84%   2h15m    51%     80%      2d
· Personal            0%       —   100%      8%     23h
■ devbox · 1m ago
· Team-1             12%   3h40m    26%      4%      5d
· ▸ fix-cache        running · Team-1 · #412 ☀️
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
REMOTE_BOARD_UP_DEVBOX='out=$("$HOME/.local/bin/devbox-cli" status 2>&1); case "$out" in *"state: running"*) exit 0;; *"state: "*) exit 1;; *credentials*|*"sso login"*) exit 2;; *) exit 3;; esac'
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
- **The up-check's exit status is a four-way answer.** The check runs as
  `bash -lc` under the poller's environment, so give it absolute paths.

  | Exit | Meaning | Poller | Row |
  |------|---------|--------|-----|
  | `0` | up | pulls | `<name> · <age> ago` |
  | `1` | down | never contacts the machine | `<name> · stopped` |
  | `2` | the check's own login expired | pulls anyway | `<name> · login expired · <age> ago` |
  | other, or a timeout | the check could not tell | pulls anyway | `<name> · <age> ago` |

  Only `1` stops the pull, so a check that fails for any other reason never
  reports a running machine as stopped. `cmd | grep -q running` does not meet
  this contract: it exits 1 when `cmd` failed and printed nothing. Match the
  output instead, as in the example above: a line that says running is `0`, any
  other state line is `1`, a login error is `2`, anything else is `3`.
- **Codex rides along.** The same pull carries the remote Codex quota rows,
  which appear in this machine's Codex statusline and `codex-top`, and under the
  board's Claude rows as `<Label>-codex`: the weekly figure and its reset, with
  dashes in the 5h and fable columns.

## Unattended jobs on a board

A board that runs unattended Claude Code sessions publishes them too, and they
render under its account rows: one line per job, and under a running job one
line per workflow that still has agents out.

On the board, each job is a tmux session working out of `~/handoffs/<slug>/`.
The `jobs-publish` timer installed with the router rewrites `~/handoffs/jobs.json`
every 30 seconds, and `accounts poll` here copies it alongside the two board
files.

- **State is observed, not reported.** A job is `running` while both its tmux
  session and the router supervising it (or, on a Codex pass, its `codex`
  process) are alive, `held` while that router is
  waiting for a window to reset (with the time it resumes), `done`, `blocked` or
  `stopped` once it writes a `report.md` (`status: blocked` on the first line
  means blocked, and `status: stopped`, which `handoff.sh stop` writes, means
  stopped), and `gone` otherwise. A wedged session cannot claim to be healthy.
- **A session outliving its router reads as `gone`, not `running`.** The router
  writes `/tmp/claude/account-router-<pid>.json` from launch, and that file
  outlives the process. The file is live only while the pid in its name runs a
  process that started before the file was last written, so a pid the system
  hands to a new process later does not revive it. A tmux session sitting at a
  bare shell after its router exited used to publish as `running` forever.
- **A failed read of the board machine never reads as a lane that ended.** When
  tmux, the process table or a lane's own files cannot be read in time, as on a
  box out of memory, the row keeps its last state, dimmed, with `reads failing 3m`
  saying for how long, or `unknown` when the board has no last state the reads
  that did answer allow. A lane reads `gone` only from a tmux read that answered.
- **A lane just sent reads `starting`, not `gone`.** The sender writes the
  lane's `job.json` before it starts the lane's tmux session, so for its first
  five minutes a lane no publish has yet seen a session for reads `starting`.
  Once a publish has listed its session, a lane whose session ends reads `gone`
  at once, and one sent again under the same name starts over.
- **A job's router is found by its tmux pane, not its directory.** A router, or
  a Codex process, belongs to a job when its pid descends from a pane of the
  tmux session named for the job's slug. A session that `cd`s into another repo
  still reads as `running`, and a router under another pane, or under none,
  never counts. Every lane runs in that session, so a job whose session has
  ended has no router to show.
- **A PR's verdict shows only for the head it was given on.** The row names the
  lane's newest PR and the review bot's verdict emoji when the bot gave it on the
  PR's current head commit, read from GitHub on every publish. A verdict on an
  older head reads `verdict stale` instead, and `conflicts` (the PR conflicts with
  its base) and `N unpushed` (commits in the lane's worktree no remote holds) say
  what else keeps the PR from being ready.
- **Moves between accounts show only when a lane is bouncing.** A running lane's
  row says `N handoffs in 1h` when its router moved it more than twice in the
  last hour; one or two moves an hour is the router riding account walls.
- **Workflow progress comes from Claude Code's own journals.** A workflow counts
  as running when agents it started have no result yet and the job is still
  running. `14/15 agents` is agents finished over agents started.
- **A board with a running job is pulled every 30 seconds** instead of
  `REMOTE_BOARD_PULL_INTERVAL`, and drops back when the last job stops.
- **A job shows where it belongs.** `job.json` may carry `origin_session`
  (the sender's Claude Code session id), `origin_pane` (the first 12 hex of
  the SHA-256 of `tmux:<server>:<pane>`, `iterm:<session uuid>` or
  `term:<TERM_SESSION_ID>`) and `origin_lane` (the sending session's name, which
  agent-workflows' `handoff.sh send` records). A job the lane list gives to a lane,
  by its slug or its PR, renders only in that lane (Lanes, below). Any other job
  renders in the lane that sent it, and in the session or terminal pane that
  sent it, so it follows the pane across `/clear` and new sessions and stays off
  every other pane. A job with no sender shows everywhere.
  `REMOTE_JOBS_SHOW_ALL=1` lists every job and marks this pane's own with
  `← this session`.
- **Recently finished jobs stay visible for half an hour**, showing their state
  and age, then drop off. A `held` job stays on screen however long the hold
  lasts — it has not finished — and reads `held · resumes 10:20pm PDT` in the
  reader's own time zone. A `blocked` job stays too: it is waiting on someone. `REMOTE_JOB_NAME_MAX` (default 20) caps the name so a
  long branch cannot widen the table.

## Lanes

A lane is a long-running session named for its work (`claude --name infra`, or
`/rename infra` in a session started by hand). Its status line shows the lane
and the PRs it owns on one row, whatever branch its checkout is on:

```
lane    agent · 29 PRs · #8882 #8879 #8878 #8877 #8680 #8594 #8593 #8283 #8218 #7188 +19 more
```

- **The list is keyed by lane name**, in `~/.accounts/lanes.json`, so it outlives
  every relaunch, handoff and pane. A session with no name, or a name no lane in
  the list has, shows no `lane` row.
- **An item has one owner.** `lane-own give <lane> <item>...` takes each item from
  whichever lane held it; `lane-own drop <item>...` takes it from every lane;
  `lane-own show [lane]` lists them. An item is a PR (`owner/repo#123`, its URL,
  or a bare `123` in the current repository or the one `--repo owner/repo`
  names), a ticket (`ABC-123`, upper case), or an unattended job
  (`photon:<slug>`).
- **The file can be edited by hand**, in the form `lane-own` writes: lower case
  lane names and `owner/repo#<number>` keys, upper case ticket ids, each a list
  of strings. `lane-own` takes a lock, and refuses a file in any other form,
  naming the field, rather than rewrite it. The status line reads such a file as
  best it can: a field of the wrong type is skipped and the rest still shows, and
  a file that does not parse says so on the `lane` row.
- **PR states are read on the account poll**, never on a render: one GraphQL
  query for every listed PR, at most every 90 seconds; `lane-own give` reads at
  once a PR no read has covered yet. States land in `~/.accounts/lane-prs.json`.
  A PR GitHub reads as merged or closed leaves the list. With no PR listed, no
  call is made.
- **Each open PR has one state**, the first that holds, in this order: queued
  (in the merge queue), auto-merge on, draft, conflicts, checks failing, changes
  requested, checks running, approved, open.
- **The row lists PRs in the order a lane acts on them**, each number in its
  state's color: checks failing and conflicts (red), changes requested (orange),
  checks running (yellow), approved (green), open (cyan), auto-merge on (green),
  queued (magenta), draft (dim), then not found (red: GitHub has no such PR) and
  not read yet (dim: no read yet, or a state this version does not know). Within
  a state, PRs keep the list's order. A PR in the session's own repository reads
  `#123`; one elsewhere, `<repo>#123`.
- **The row never wraps.** PRs that don't fit the status line's width end it as
  `+N more`; the ones cut are the least pressing. `lane-own show <lane>` lists
  every PR with its state, and the lane's tickets, which the row doesn't draw.
- **States the poll stopped refreshing say so**: `states 25m old` once the last
  successful read is more than ten minutes old, and `states never read: <why>`
  when no read has succeeded. `lane-own show` prints the last read's error.
- **A lane sees its unattended jobs**: a job whose slug or PR the list gives
  the lane, and any job the list gives no lane that the lane sent. A job the
  list gives a lane shows in that lane alone, even one with no sender.

## Being told the session was moved

A cross-account move stops the running process and relaunches it on `--resume`.
Claude Code adds "Continue from where you left off." only when the transcript
ends mid-turn, so a session that was idle between turns never learns it moved —
and any in-process workflow, subagent or background task died with the old
process. The relaunch carries a first message naming the accounts, the reason,
and what was lost, so an unattended session can restart what was in flight.
`ACCOUNTS_HANDOFF_NOTICE=0` turns it off; the setting is read at each handoff. A
session with no transcript to resume never gets one.

Claude Code will not start in a directory that no longer exists, but a running
session keeps working after its directory is removed (a lane that cleaned up its
own worktree). The supervisor then treats every account as unable to take the
session, since no other directory is the session's own and a path re-created at
the same place may be another checkout:

- No planned move is made: the session works on until it exits. A wall is
  handled as when no account is free: the session runs on, or stops under
  `ACCOUNTS_STRICT_QUOTA=1` or `ACCOUNTS_HOLD_FOR_RESET=1`.
- A hold ends at its next wake (two minutes past the soonest reset, or 15
  minutes at most) instead of resuming.
- When the session ends on a limit another account could take, it is not
  relaunched. A Fable wall the child crossed itself, on the fallback model the
  supervisor gave it, is no limit here.
- The session's lease stays on the account it runs on, so `accounts forget` or
  `accounts move` of that account waits up to its timeout for the session to
  end, and refuses if it has not. Under `ACCOUNTS_STRICT_QUOTA=1` or
  `ACCOUNTS_HOLD_FOR_RESET=1` the forget is a wall like any other: the session
  stops and the forget completes.

Each of those endings (the stop, the hold, the limit exit) and a move whose
directory goes while the child is being stopped print one line naming the
`claude --resume <id>` to run from an existing directory, or no id when the
supervisor finds no saved transcript for it (a held `claude -p` launch never
names one). A supervised launch from a removed directory exits with the same
line; a `claude -p` launch that is not held leaves the refusal to Claude.

A move that is not a plan wall (an `accounts set`, a mode change, a Fable
departure) waits while the session has a Workflow in flight: a run its
transcript launched whose transcript directory exists and whose completed state
file does not. The supervisor records the wait in its state file as
`pending_handoff` (target label, since when, the run ids) and moves once the
runs finish or `ACCOUNTS_HANDOFF_GRACE_S` (default 1800) has passed. A wall
moves at once, and so does a session whose account was removed (`accounts forget`
or `move`): its login is gone, so waiting for the runs is moot. Whenever a move does stop runs, the notice names each run id and
script path and the `Workflow({scriptPath, resumeFromRunId})` call that resumes
it, so the relaunched session picks the work up from the agents that finished.

`accounts brief <session-id> --prompt "<one line>" [--model <model>]` leaves a
session its next relaunch's first message and model, written to
`~/.accounts/handoff-briefs/<session-id>.json`. The supervisor uses it once, on
the next handoff of any kind: the prompt follows the notice, and the model
replaces the one the session was running. Write one before a planned switch
(`accounts set`) so an unattended session resumes its work on the model you
want, with nothing typed into the terminal. The prompt a session was launched with stays behind on a
relaunch that resumes — it is already in the transcript, and a second prompt
beside the notice makes the CLI submit neither.

