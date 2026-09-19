# Agent Metrics

Agent Metrics is an opt-in, local-first history and dashboard add-on for Claude
Code and Codex. It is not installed or started by either default installer.
Nothing is collected until one of its explicit commands is run. It requires
Python 3.11 or newer; set `AGENT_METRICS_PYTHON` to a compatible interpreter
when the system `python3` is older.

```bash
bin/agent-metrics init
bin/agent-metrics sync --max-lines 5000
bin/agent-metrics watch --interval 60 --max-lines 5000
bin/agent-metrics serve
# In another terminal, only when you want a browser window:
bin/agent-metrics open
```

## Commands

`init` creates private runtime storage and a configuration file. On macOS the
default is `~/Library/Application Support/statusline/agent-metrics/`; on Linux
it is `${XDG_DATA_HOME:-~/.local/share}/statusline/agent-metrics/`. Override it
with `--data-dir` or `AGENT_METRICS_DATA_DIR`. Runtime data is never written to
this repository.

`sync` incrementally scans local Claude Code and Codex JSONL files into raw,
event-level SQLite rows and rebuilds derived one-minute metrics. Repeated scans
are idempotent. `--max-lines` bounds one invocation; omit it for an unlimited
manual backfill. Bounded scans reserve capacity for appended live files and for
both providers while rotating through older sources by salted source ID.
`watch` is an explicit foreground loop that defaults to 5,000 lines every 60
seconds, measured after each completed cycle. It prints live/backfill progress
and remaining file/byte counts; Ctrl-C stops it cleanly. With
`AGENT_METRICS_RECORDER=1` in `statusline.conf`, `macos/launchd/install-agents.sh`
keeps `watch` running as a launchd agent (`com.claude-agent-metrics-watch`) in
place of the token scanner; otherwise nothing autostarts it.

## Dashboard

The local dashboard polls the SQLite database for a stacked token timeline with
selectable token series, one-minute raw or trailing moving-average views, a
trailing-day hourly/cumulative view, provider/account/model/effort/session/agent
filters, account and model totals, parent/child agent drilldown, compactions, tool outcomes and durations,
turn latency, quota snapshots, and explicitly exposed cost. `serve` does not
scan automatically and binds only to a loopback address; non-loopback binds are
rejected. Its HTML, CSS, and JavaScript have no network dependencies or
analytics.
`open` passes a private local capability to the browser; dashboard API reads
without that capability are rejected, including requests from other local processes.

## Privacy

The database stores numerical metadata plus provider, model, effort, opaque
session/request/call IDs, and tool names/statuses. It never stores prompts,
transcript text, tool arguments or output, source text, source paths,
credentials, token values, emails, or account-holder names. Account and source
identities use a local salt. Claude attribution matches each event timestamp to
`session-accounts.json` using half-open `[from,to)` spans; the organization ID
participates in the account hash. Codex reads only the explicit current
`account_id` field from `auth.json`; it never decodes or stores access, refresh,
or identity tokens.

## Configuration

Configuration lives in the runtime directory's `config.toml`; the generic
template is [`config/agent-metrics.toml.example`](../config/agent-metrics.toml.example).
Source paths, account aliases, pricing metadata, retention, bind address, and
port are configurable. Agent Metrics can reuse declared short Claude account
labels from `ACCOUNT_LABELS` in a configurable `statusline.conf`; explicit
`[account_aliases]` entries win, and the feature can be disabled. Patterns,
emails, and organization IDs are matched only in memory and are never stored.
Pricing is not applied to infer event cost.

## Quota inference

Optional `[account_tiers]` entries map declared account labels to `5x` or
`20x`. Agent Metrics records minute quota observations from the shared account
snapshot and incrementally backfills the existing Claude utilization history
when a declared label matches in memory. It excludes stale, pending-reset,
reset-crossing, and zero/negative-utilization intervals, then compares tracked
token deltas with positive five-hour utilization deltas by plan cohort, model,
and reasoning effort. The dashboard reports samples, dispersion, and observed
token ranges as a **tracked-token equivalent**. This is empirical local data,
not an Anthropic-published fixed quota; other clients and untracked usage can
bias it. For accounts without safe declared-label history, inference starts
with new shared snapshots.

## Capture limits

Current capture limits: Codex local history does not expose historical account
handoffs, so newly ingested Codex rows receive the account active at their first
sync. Some Claude records omit reasoning effort, context limits, compaction
details, quota, or cost; those fields remain empty rather than being inferred.
Tool duration is available only when matching start/end records are present.

