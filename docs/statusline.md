# Claude Code status line

`bin/statusline.sh` is one bash script. Claude Code runs it on every refresh and it prints the dashboard.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AndrewTKent/statusline/main/install.sh | bash
```

Or via npm:
```bash
npx @andrewkent/claude-statusline install
```

Or manually — copy the script, add one key to settings:
```bash
cp bin/statusline.sh ~/.claude/statusline.sh && chmod +x ~/.claude/statusline.sh
```
```json
{ "statusLine": { "type": "command", "command": "~/.claude/statusline.sh", "padding": 0, "refreshInterval": 60 } }
```

Restart Claude Code. Done.

**Requires:** [`jq`](https://jqlang.github.io/jq/) &middot; Claude Code (logged in) &middot; Optional: [`gh`](https://cli.github.com/) for PR badges

## What you see

`default` renders one labeled row per fact — the sample at the top of the [README](../README.md). Every row below `repo` is conditional on data actually being available:

| Row | Shown when | What it shows |
|-----|-----------|----------------|
| `model` | always | Model + effort (`.low`/`.medium`/`.high`/`.xhigh`/`.max`/`.ultracode`; shared snapshot mode writes `· low` and so on) + `⚡fast` when Settings' fast mode is on |
| `time` | session duration available | Wall-clock (`⏱ 24:12`); adds `idle Nm` after 30s with no user turn |
| `account` | account resolved | Tag from `ACCOUNT_LABELS`, colored per `LABEL_COLORS` |
| `repo` | always | Primary repository name |
| `tree` | the checkout is a linked worktree | Worktree name |
| `branch` | the checkout is in Git | Branch, dirty `*`, and `↑`/`↓` divergence |
| `pr` | the checkout maps to an open PR | PR number and title for the checked-out branch or detached PR head |
| `context` | always | Context-window fill — 15-dot sweet-spot bar (blue <30%, green 30–70%, yellow 70–85%, red 85%+) |
| `session` | 5h rate-limit data available | 5h window used, 15-dot bar + `resets <time>` |
| `weekly` | 7-day rate-limit data available | 7-day window used, 15-dot bar + `resets <date>` |
| `fable` | account has a per-model weekly cap | That cap's usage, 15-dot bar (label = the scoped model; opt-out `SHOW_FABLE_ROW=0`) |
| `budget` | `DAILY_BUDGET` set | Spend vs. cap, 10-dot bar |
| `tokens` | scan data available | All-time work/personal token ratio, 10-dot bar (opt-out `SHOW_TOKENS_ROW=0`) |
| goal row | `CHALLENGE_GOAL_M` set (see script header comment) | Progress toward a token goal, labeled `CHALLENGE_LABEL` (opt-out `SHOW_CHALLENGE_ROW=0`) |
| `bounty` | bounty config set and uncleared | ETA to a work-token floor (opt-out `SHOW_BOUNTY_ROW=0`) |
| `usage` | scan data available | Today / this session / lifetime totals, human-formatted |
| `stack` | `SHOW_BACKENDS_ROW=1` | Live snapshot across Claude/Codex/remote agents (`bin/live-state.py`) |
| per-account rows | `SHOW_ACCOUNT_RESETS=1` | One row per tracked account: 5h%, reset, week%, fable%, reset, work-unit cap |

PR badge states: `[draft]`, `[PR✗]` checks failing, `[PR△]` changes requested, `[PR✓]` approved, `[PR⋯]` checks pending, `[PR]` open with no strong signal either way.

### Token tracking

`tokens` and `usage` are both fed by `bin/scan-tokens.py`'s background scan of every session JSONL, cached to `~/.claude/token-scan-summary.json` (small, preferred) or `~/.claude/token-scan-cache.json` (full, fallback) — rescanned in the background whenever that cache is older than 180s.

- **`tokens`** — all-time work/personal ratio (cyan = work, magenta = personal), classified per-request by the `WORK_PATHS`/`WORK_KEYWORDS` vs `PERSONAL_PATHS`/`PERSONAL_KEYWORDS` rules in `statusline.conf`
- **`usage`** — today / this session / lifetime, human-formatted (k/M/B)
- Subagent (Agent tool) tokens are scanned separately (30s cache) and only break out in the optional token-goal row

### Account tagging

All cost and token ledgers are tagged with your account label (e.g., `work` or `personal`), derived from your OAuth email via `ACCOUNT_LABELS`. This lets you aggregate spend by account after the fact. Two related but distinct dimensions live inside the token scanner itself: `EMAIL_PAYER_MAP` (which plan paid) and the work/personal path/keyword classifier (what the work was) — see [configuration.md](configuration.md).

### Shared account snapshot

Set `SHARED_ACCOUNT_SNAPSHOT=1` to make account and quota rendering read-only and snapshot-only. Run `accounts poll` for one refresh or `accounts watch --interval 60` as an explicit foreground loop. The renderer reads `~/.accounts/statusline-snapshot.json` once, maps the current account only through `ACCOUNTS_ROUTED_LABEL`, and displays only declared short labels. It does not inspect credentials, call the profile or usage APIs, write shared ledgers, or start the full token scanner. Missing, stale, pending-reset, and error data remain unknown or visibly stale; they are never rendered as zero. `SHARED_ACCOUNT_SNAPSHOT_FILE` and `SHARED_ACCOUNT_SNAPSHOT_MAX_AGE` are configurable.

Shared mode uses its own lightweight presentation: the default layout keeps the
account board, while compact terminal formats use one line. It still refreshes
the terminal title and router state, but skips legacy notifications and history writes.

Claude Code's `statusLine.refreshInterval` controls renderer cadence. A 60-second interval matches the foreground account watcher and avoids repeated work for minute-resolution quota data.

### Terminal tab titles

The script sets the terminal tab title (via ANSI escape) to `repo-name` on main/master, or `repo-name (branch)` on feature branches. Useful in Zed, iTerm2, and other terminals to tell sessions apart at a glance.

### Notifications

macOS Notification Center alerts are off by default; opt in with
`STATUSLINE_NOTIFY=1` (exported, or set in `~/.claude/statusline.conf`).
When enabled they fire once per threshold, deduped:
- **Rate limit** at 80%, 90%, 95%
- **Context** at 80%, 95%
- **Budget** at 90%, 100%

### Automatic account detection

When you `/login` inside a routed profile, the status bar detects the credential change before writing its ledgers, refreshes the profile, and updates the rate limits and account label on the next render. Sessions using that same native profile see the refreshed login. If the login belongs to another stored account, the router repairs the current profile and pins the logged-in account at the active policy scope: pane-local for a pane pin, otherwise global.

## How it works

The token-scan engine, remote boards and unattended jobs are covered in [`bin/ARCHITECTURE.md`](../bin/ARCHITECTURE.md).

Claude Code pipes a JSON status blob into the script via stdin on every tool call. The script:

1. **Parses** model, cost, context, session metadata (single `jq` call)
2. **Detects** credential changes and validates changed profile identity before any account-tagged ledger write
3. **Resolves** the account label from the OAuth profile cache and updates the daily cost/token ledgers in `~/.claude/`
4. **Scans** subagent JSONL files for the current session (cached 30s) and reads `token-scan-summary.json` (fallback: `token-scan-cache.json`) for the work/personal token split — kicks off a background `scan-tokens.py` rescan when that cache is stale (>180s)
5. **Builds** the git/PR segment (branch, dirty, ahead/behind, `gh pr view` cached 90s) and the effort/fast-mode/focus badges
6. **Refreshes** rate limits and profile from Anthropic's OAuth API in the background (usage cached 60s, profile cached 5min)
7. **Interpolates** usage between polls — tracks velocity across consecutive API responses for smooth fractional percentages
8. **Builds** the budget row (if `DAILY_BUDGET` is set) and the optional multi-account reset board (if `SHOW_ACCOUNT_RESETS=1`)
9. **Sets** terminal tab title to repo + branch
10. **Checks** notification thresholds when `STATUSLINE_NOTIFY=1` (fires once per crossing, deduped)
11. **Renders** in your chosen format, falling back to `narrow` under `NARROW_THRESHOLD` columns

### Architecture

```
Claude Code                    statusline.sh
    │                              │
    ├─ stdin JSON ────────────────►│ parse (jq)
    │                              │
    │                              ├─► changed credential: fetch profile (≤2s)
    │                              ├─► resolve account label (profile cache)
    │                              ├─► update daily-cost.json    (tagged w/ account)
    │                              ├─► update daily-tokens.json  (tagged w/ account)
    │                              ├─► scan subagent JSONL files (cached 30s)
    │                              ├─► read token-scan-summary.json (fallback: token-scan-cache.json)
    │                              ├─► background: fetch /api/oauth/usage (cached 60s)
    │                              ├─► background: refresh /api/oauth/profile (cached 5min)
    │                              ├─► check notification thresholds
    │                              ├─► set terminal tab title (\033]0;repo (branch)\007)
    │                              │
    │  stdout ANSI ◄──────────────├─► render (default|compact|narrow|sigil|sparkline|rprompt|iterm2)
    │                              │
    ├─ /tmp/claude/*.json ────────►│ macOS apps read these
```

### Performance

| Concern | How it's handled |
|---------|-----------------|
| Network latency | Background refreshes; a changed credential can block up to 2s for identity validation |
| Concurrent sessions | Lock file with stale-PID detection (auto-cleanup at 30s) |
| Git dirty check | `git diff-index --quiet HEAD` (faster than `git status`) |
| PR status | Repository-scoped `gh` lookup cached 90s, background-refreshed |
| Ledger writes | Atomic (mktemp + mv) |
| Account switch | OAuth token hash + credential mtime tracking, synchronous identity validation before ledger writes |
| Subagent scan | File-based cache with 30s TTL, scoped to current session |
| Token bar | `jq` read from `token-scan-summary.json` (fallback: `token-scan-cache.json`); the actual JSONL rescan runs in the background via `scan-tokens.py`, never inline |
| Shared account snapshot | One stable inode+mtime read; no credential/profile/usage calls or shared-ledger writes |

### Files

| File | Purpose | Lifetime |
|------|---------|----------|
| `~/.claude/statusline.sh` | The script (or symlink) | Permanent |
| `~/.claude/statusline.conf` | Config | Permanent |
| `~/.claude/daily-cost.json` | Daily cost ledger (account-tagged) | Resets daily |
| `~/.claude/daily-tokens.json` | Daily token tracker (account-tagged) | Resets daily |
| `~/.claude/token-scan-summary.json` | Small token-scan summary (preferred read) | Persistent |
| `~/.claude/token-scan-cache.json` | Full token-scan cache (fallback read) | Persistent |
| `~/.claude/account-resets.json` | Multi-account reset ledger (`SHOW_ACCOUNT_RESETS`) | Persistent |
| `~/.claude/account-caps.json` | Per-account work-unit caps, written by `bin/derive-cap.py` | Persistent |
| `~/.claude/utilization-history.jsonl` | Raw utilization samples backing the account board | Rolling |
| `~/.claude/session-history.jsonl` | Sparkline history (account + subagent fields) | Rolling 100 entries |
| `~/.claude/rprompt.txt` | Zsh RPROMPT (`rprompt` format) | Updated each render |
| `~/.claude/usage-ledger.json` | Durable per-day/per-model token ledger (`bin/usage-ledger.py`) | Permanent |
| `~/.claude/statusline-tz` | Optional timezone override for reset-time display | Permanent |
| `~/.accounts/statusline-snapshot.json` | Private declared-label routing and quota snapshot (`SHARED_ACCOUNT_SNAPSHOT=1`) | Written only by explicit `accounts poll`/`accounts watch` |
| `~/.accounts/remote/<name>/` | Another machine's pulled board: its snapshot, its Codex usage, and the pull's `meta.json` | Rewritten by `accounts poll` |
| `~/.claude/.credentials.json` | Claude Code's own OAuth credential — read-only, mtime-tracked | Claude-Code-managed |
| `/tmp/claude/statusline-usage-cache-<profile>.json` | Account-keyed rate-limit API cache | 60s TTL |
| `/tmp/claude/statusline-profile-cache-<profile>.json` | Account-keyed profile API cache | 5min TTL |
| `/tmp/claude/statusline-usage-prev-<profile>.json` | Account-keyed previous poll, for interpolation | Updated each poll |
| `/tmp/claude/statusline-{usage,profile}-cache.json` | Current-profile aliases for companion apps | Updated each render |
| `/tmp/claude/statusline-subagent-<sid>.txt` | Subagent token cache per session | 30s TTL |
| `/tmp/claude/ctx-history-<sid>.txt` | Context-fill samples, for the fill-ETA calc | Rolling |
| `/tmp/claude/statusline-pr-<repo-ref-key>.json` | PR status cache | 90s TTL |
| `/tmp/claude/statusline-pr-<repo-ref-key>.json.lock` | PR refresh lock | Persistent file, transient lock |
| `/tmp/claude/statusline-raw.json` | Raw status blob, for macOS apps | Updated each legacy render; not used in shared snapshot mode |
| `/tmp/claude/statusline-notif-state.json` | Notification dedup state | Per-threshold |
| `/tmp/claude/statusline-refresh-<profile>.lock` | Account-keyed background refresh lock | Transient |
| `/tmp/claude/statusline-creds-mtime-<profile>` | Account-keyed credential mtime detector | Persistent |
| `/tmp/claude/statusline-token-hash-<profile>` | Account-keyed OAuth token hash detector | Persistent |

