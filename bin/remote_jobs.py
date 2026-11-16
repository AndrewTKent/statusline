#!/usr/bin/env python3
"""Publish what this machine's unattended Claude jobs are doing, for a laptop to pull.

A job is one tmux session working out of `~/handoffs/<slug>/`. Everything here is
derived from what is observable on this machine — the tmux panes and the
process tree under each one, the report the job wrote, the git HEAD
of its worktree, the router's state file and Claude Code's workflow journals —
so a wedged session cannot report itself healthy. The one thing read from off the
machine is each lane PR's state on GitHub, in one query per publish. A read that
fails or times out says nothing about what is there, so it never makes a lane `gone`:
the lane keeps the state the last publish gave it, marked stale. The file is
replaced atomically: a reader sees one version or the next.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import accounts
import codex_accounts

JOBS_FILE = "jobs.json"
JOB_FILE = "job.json"
REPORT_FILE = "report.md"
QUEUE_FILE = "lane-queue.txt"
AFTER_FILE = "after"
OUTBOX_FILE = "outbox.jsonl"
PR_FILE = "pr.json"
PR_STATES = {"OPEN", "MERGED", "CLOSED"}
PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([0-9]+)")
VERDICTS = ("☀️", "🌤️", "⛅", "🌧️")
REVIEW_BOT = {"claude", "claude[bot]"}
# A verdict whose review fails to post goes up as a plain comment instead, so both are read.
POST_FIELDS = "author { login } body"
PR_FIELDS = (
    f"number state url headRefOid mergeable"
    f" reviews(last: 20) {{ nodes {{ {POST_FIELDS} submittedAt commit {{ oid }} }} }}"
    f" comments(last: 20) {{ nodes {{ {POST_FIELDS} createdAt }} }}"
)
REPORT_STATES = {"status: blocked": "blocked", "status: stopped": "stopped"}
HANDOFF_WINDOW_S = 3600
# A move or two an hour is the router riding account walls; a third means it is bouncing between walled accounts.
HANDOFF_CHURN_MAX = 2
MESSAGES_KEPT = 20
MESSAGE_TEXT_MAX = 1000
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SENT_FILES = {"brief.md", "launch.sh"}
QUOTA_WAIT_FILE = "quota-wait.json"
# Seconds past its next poll a launcher's quota-wait.json still counts; agent-workflows' handoff.sh uses the same.
QUOTA_WAIT_SLACK_S = 120
# The box's own account snapshots, the same two files a laptop pulls from it.
CLAUDE_SNAPSHOT = Path(".accounts") / "statusline-snapshot.json"
CODEX_USAGE = Path(".codex-accounts") / "usage.json"
# Where each router stops picking an account: Claude's last resort refuses one at its hard wall,
# Codex one at its limit.
CODEX_WALL_PCT = 100.0
# The pane reader agent-workflows installs beside the lanes; the box's lane watch reads panes with it too.
PANE_READER = "lane-pane.sh"
ROUTER_STATE = re.compile(r"account-router-(\d+)\.json")
WORKFLOW_NAME = re.compile(r"name:\s*['\"]([^'\"]+)['\"]")
SCRIPT_HEAD_BYTES = 2048
COMMAND_TIMEOUT_S = 5
# Past this a publish starts no more commands, so on a saturated box it still ends inside its 30s period.
PUBLISH_BUDGET_S = 20
# What tmux says when no server listens on its socket: then there are no sessions to list.
NO_TMUX_SERVER = re.compile(r"no server running on |error connecting to .* \(No such file or directory\)")
# What a lane keeps from the last publish while a read its state depends on fails.
CARRIED_FIELDS = (
    "state", "held_until", "queue_position", "after", "quota", "account",
    "handoffs", "handoffs_last_hour", "workflows",
)
PROC = Path("/proc")
STAT_STARTTIME = 19  # field 22 of /proc/<pid>/stat, counted after the ")" that ends field 2

# When the running publish stops starting commands; main() sets it.
deadline = math.inf


class ReadFailed(Exception):
    """A read of this machine that failed or timed out: it says nothing about what is there."""


class Process(NamedTuple):
    parent: int
    command: str
    started: float


class Machine(NamedTuple):
    """One publish's reads of this machine. `failed` names the reads that failed; their fields are empty."""
    queue: list[str]
    panes: dict[str, set[int]]
    table: dict[int, Process]
    routers: dict[str, dict]
    unread_routers: set[str]
    codex: set[str]
    failed: set[str]


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def handoffs_dir() -> Path:
    return Path(os.environ.get("HANDOFFS_DIR") or home() / "handoffs")


def read_text(path: Path) -> str | None:
    """None when the file is not there; ReadFailed when it is and could not be read."""
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ReadFailed(path.name) from error


def attempt(failed: set[str], name: str, read, empty):
    """What `read` returns, or `empty`, with `name` added to `failed`, when it raised ReadFailed."""
    try:
        return read()
    except ReadFailed:
        failed.add(name)
        return empty


def queued_slugs() -> list[str]:
    """Lanes the box's admission gate is holding, first in line first."""
    lines = (read_text(handoffs_dir() / QUEUE_FILE) or "").splitlines()
    return [line.strip() for line in lines if line.strip()]


def router_state_dir() -> Path:
    return Path(os.environ.get("ACCOUNTS_ROUTER_STATE_DIR") or "/tmp/claude")


def config_roots() -> list[Path]:
    """Every Claude Code config dir a job could be running under, routed or not."""
    raw = os.environ.get("CLAUDE_CONFIG_ROOTS")
    if raw:
        return [Path(part) for part in raw.split(":") if part]
    return [home() / ".claude", *sorted((home() / ".accounts" / "profiles").glob("*"))]


def run(argv: list[str]) -> subprocess.CompletedProcess:
    """The finished command, whatever its exit; ReadFailed when it could not start or answer in time."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise ReadFailed(argv[0])
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=min(COMMAND_TIMEOUT_S, left)
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReadFailed(argv[0]) from error


def pane_pids() -> dict[str, set[int]]:
    """The pid of the shell in each pane, by the tmux session it belongs to; none when no server runs."""
    result = run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"])
    if result.returncode != 0:
        if NO_TMUX_SERVER.match(result.stderr):
            return {}
        raise ReadFailed("tmux")
    panes: dict[str, set[int]] = {}
    for line in result.stdout.splitlines():
        pid, _, session = line.partition(" ")
        panes.setdefault(session, set()).add(int(pid))
    return panes


def boot_time() -> float:
    lines = (PROC / "stat").read_text().splitlines()
    return next(float(line.split()[1]) for line in lines if line.startswith("btime "))


def processes() -> dict[int, Process]:
    """Every process on this machine, by pid."""
    try:
        boot = boot_time()
        entries = sorted(PROC.iterdir())
    except (OSError, StopIteration) as error:
        raise ReadFailed("processes") from error
    ticks = os.sysconf("SC_CLK_TCK")
    table = {}
    for process in entries:
        if not process.name.isdigit():
            continue
        try:
            stat = (process / "stat").read_text(errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            continue  # it exited after the listing
        except OSError as error:
            raise ReadFailed("processes") from error
        # The command name can itself hold spaces and parentheses, so split at the last ")".
        head, _, tail = stat.rpartition(")")
        fields = tail.split()
        table[int(process.name)] = Process(
            int(fields[1]),
            head.partition("(")[2],
            boot + int(fields[STAT_STARTTIME]) / ticks,
        )
    return table


def descends_from(pid: int, roots: set[int], table: dict[int, Process]) -> bool:
    seen = set()
    while pid not in roots:
        if pid in seen or pid not in table:
            return False
        seen.add(pid)
        pid = table[pid].parent
    return True


def owners(pid: int, panes: dict[str, set[int]], table: dict[int, Process]) -> set[str]:
    """The tmux sessions whose panes the process runs under; a grouped session shares panes."""
    return {session for session, roots in panes.items() if descends_from(pid, roots, table)}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def file_mtime(path: Path) -> float | None:
    """None when the file is not there; ReadFailed when it is and could not be read."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ReadFailed(path.name) from error


def as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def git_output(worktree: str, *args: str) -> str:
    """What git prints in the worktree; empty once the worktree is removed, and ReadFailed when git
    fails in one that is there."""
    result = run(["git", "-C", worktree, *args])
    if result.returncode == 0:
        return result.stdout.strip()
    if Path(worktree).exists():
        raise ReadFailed("git")
    return ""


def git_head(worktree: str) -> str:
    return git_output(worktree, "rev-parse", "--short", "HEAD") if worktree else ""


def unpushed_commits(worktree: str) -> int:
    """Commits the worktree's HEAD holds that no remote-tracking ref does."""
    if not worktree:
        return 0
    return as_int(git_output(worktree, "rev-list", "--count", "HEAD", "--not", "--remotes"))


def report_status(path: Path) -> str | None:
    """None when the job wrote no report; otherwise done unless its first line says blocked or stopped."""
    text = read_text(path)
    if text is None:
        return None
    lines = text.splitlines()
    first = lines[0].strip().lower() if lines else ""
    return REPORT_STATES.get(first, "done")


def live_router_pid(path: Path, table: dict[int, Process]) -> int:
    """A state file outlives its router and the pid in its name is reused, so the file is live
    only while that pid runs a process started before the file was last written; 0 otherwise."""
    match = ROUTER_STATE.fullmatch(path.name)
    process = table.get(int(match.group(1))) if match else None
    if process is None:
        return 0
    written = file_mtime(path)
    if written is None or process.started > written:
        return 0
    return int(match.group(1))


def routers_by_session(
    panes: dict[str, set[int]], table: dict[int, Process]
) -> tuple[dict[str, dict], set[str]]:
    """Each live router's state, by the tmux session it runs under, wherever it has cd'd to, and the
    sessions under which a live router's state could not be read. Every lane runs in a session named
    for its slug, so a router under no pane is no job's."""
    try:
        paths = sorted(router_state_dir().iterdir())
    except FileNotFoundError:
        return {}, set()
    except OSError as error:
        raise ReadFailed("routers") from error
    routers: dict[str, dict] = {}
    unread: set[str] = set()
    for path in paths:
        try:
            pid = live_router_pid(path, table)
        except ReadFailed:
            # Only a file whose pid is running gets this far.
            unread |= owners(int(ROUTER_STATE.fullmatch(path.name).group(1)), panes, table)
            continue
        sessions = owners(pid, panes, table) if pid else set()
        if not sessions:
            continue
        try:
            text = read_text(path)
            state = json.loads(text) if text is not None else None
        except (ReadFailed, ValueError):
            unread |= sessions
            continue
        if not isinstance(state, dict):
            continue
        for session in sessions:
            routers.setdefault(session, state)
    return routers, unread


def session_dir(session_id: str) -> Path | None:
    if not session_id:
        return None
    for root in config_roots():
        for candidate in root.glob(f"projects/*/{session_id}"):
            if candidate.is_dir():
                return candidate
    return None


def workflow_script(session: Path, run_id: str) -> Path | None:
    return next((session / "workflows" / "scripts").glob(f"*-{run_id}.js"), None)


def workflow_name(script: Path | None, run_id: str) -> str:
    if script is None:
        return run_id
    try:
        with script.open() as handle:
            head = handle.read(SCRIPT_HEAD_BYTES)
    except OSError:
        return run_id
    match = WORKFLOW_NAME.search(head)
    if match:
        return match.group(1)
    return script.name[: -len(f"-{run_id}.js")]


def agent_ids(journal: Path) -> dict[str, set[str]]:
    seen = {"started": set(), "result": set(), "failed": set()}
    try:
        lines = journal.read_text().splitlines()
    except OSError:
        return seen
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        bucket = seen.get(str(event.get("type")))
        agent_id = event.get("agentId")
        if bucket is not None and agent_id:
            bucket.add(str(agent_id))
    return seen


def journals(session: Path | None) -> list[Path]:
    if session is None:
        return []
    return sorted((session / "subagents" / "workflows").glob("*/journal.jsonl"))


def workflows_for(session: Path | None, alive: bool) -> list[dict]:
    """Running means agents are still outstanding and the session that owns them
    is alive. Journal lines carry no timestamps and an agent can work for an hour
    without writing one, so mtime cannot tell working apart from killed."""
    rows = []
    for journal in journals(session):
        run_id = journal.parent.name
        seen = agent_ids(journal)
        outstanding = seen["started"] - seen["result"] - seen["failed"]
        script = workflow_script(session, run_id)
        rows.append(
            {
                "name": workflow_name(script, run_id),
                "started_at": int(mtime(script) if script else mtime(journal)),
                "agents_started": len(seen["started"]),
                "agents_done": len(seen["result"]),
                "agents_failed": len(seen["failed"]),
                "running": alive and bool(outstanding),
            }
        )
    return rows


def last_change(directory: Path, session: Path | None) -> int:
    """What the job wrote, not what it was sent: a sweep over every launcher is not activity."""
    written = [path for path in children(directory) if path.name not in SENT_FILES]
    return int(max((mtime(path) for path in [*written, *journals(session)]), default=0.0))


def codex_sessions(panes: dict[str, set[int]], table: dict[int, Process]) -> set[str]:
    """Codex runs unrouted, so a live codex process under a pane stands in for a router."""
    codex = [pid for pid, process in table.items() if process.command == "codex"]
    return {session for pid in codex for session in owners(pid, panes, table)}


def lane_messages(path: Path) -> list[dict]:
    """The last lines a lane posted with `handoff.sh notify`, oldest first. Which ones a session
    has read is known only where it reads them, so recent lines are all published and the
    reader delivers each id once."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message_id = str(entry.get("id") or "")
        if not MESSAGE_ID.fullmatch(message_id):
            continue
        text = CONTROL_CHARS.sub(" ", str(entry.get("text") or ""))
        messages.append(
            {"id": message_id, "ts": as_int(entry.get("ts")), "text": text[:MESSAGE_TEXT_MAX]}
        )
    return messages[-MESSAGES_KEPT:]


def lane_pr(path: Path) -> dict:
    """The lane's newest PR as the box's lane-retire timer last read it from GitHub, or {}. Its verdict
    is not carried: only a read of the PR's current head says whether one still holds."""
    data = read_json(path, {})
    if not isinstance(data, dict) or as_int(data.get("number")) <= 0:
        return {}
    state = str(data.get("state") or "")
    return {
        "number": as_int(data.get("number")),
        "state": state if state in PR_STATES else "",
        "url": str(data.get("url") or ""),
        "verdict": "",
        "verdict_stale": False,
        "conflicts": False,
        "checked_at": as_int(data.get("checked_at")),
    }


def pr_key(pr: dict) -> tuple[str, str, int] | None:
    """Owner, repository and number, from the PR's URL."""
    match = PR_URL.fullmatch(pr.get("url") or "")
    return (match.group(1), match.group(2), int(match.group(3))) if match else None


def read_prs(keys: set[tuple[str, str, int]]) -> dict[tuple[str, str, int], dict] | None:
    """Every lane PR as GitHub has it now, in one GraphQL query; None when the read fails."""
    if not keys:
        return {}
    params, fields, variables, aliases = [], [], [], {}
    for i, (owner, name) in enumerate(sorted({(owner, name) for owner, name, _ in keys})):
        params += [f"$o{i}: String!", f"$n{i}: String!"]
        variables += ["-f", f"o{i}={owner}", "-f", f"n{i}={name}"]
        numbers = sorted(number for o, n, number in keys if (o, n) == (owner, name))
        aliases.update({(owner, name, number): (f"r{i}", f"p{number}") for number in numbers})
        prs = " ".join(f"p{number}: pullRequest(number: {number}) {{ {PR_FIELDS} }}" for number in numbers)
        fields.append(f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ {prs} }}")
    query = f"query({', '.join(params)}) {{ {' '.join(fields)} }}"
    try:
        result = run(["gh", "api", "graphql", "-f", f"query={query}", *variables])
        data = json.loads(result.stdout)["data"] if result.returncode == 0 else None
    except (ReadFailed, ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    found = {}
    for key, (repository, pr) in aliases.items():
        node = (data.get(repository) or {}).get(pr)
        if isinstance(node, dict):
            found[key] = node
    return found


def on_head(post: dict, head: str) -> bool:
    """A review names the commit it reviewed; a comment names it in its body."""
    commit = post.get("commit") if isinstance(post.get("commit"), dict) else {}
    return bool(head) and (commit.get("oid") == head or head in str(post.get("body") or ""))


def nodes(connection) -> list[dict]:
    found = connection.get("nodes") if isinstance(connection, dict) else None
    return [node for node in found or [] if isinstance(node, dict)]


def solei_verdict(pr: dict) -> tuple[str, bool]:
    """The emoji opening the review bot's newest verdict on the PR's head commit, or '' and whether it
    gave one only on an older head: that one says nothing about what the PR holds now."""
    head = str(pr.get("headRefOid") or "")
    posts = [(str(node.get("submittedAt") or ""), node) for node in nodes(pr.get("reviews"))]
    posts += [(str(node.get("createdAt") or ""), node) for node in nodes(pr.get("comments"))]
    stale = False
    for _, post in sorted(posts, key=lambda pair: pair[0], reverse=True):
        author = post.get("author") if isinstance(post.get("author"), dict) else {}
        if author.get("login") not in REVIEW_BOT:
            continue
        body = str(post.get("body") or "").lstrip()
        # Bodies may drop the emoji variation selector.
        verdict = next((v for v in VERDICTS if body.startswith(v.rstrip("\ufe0f"))), "")
        if not verdict:
            continue
        if on_head(post, head):
            return verdict, False
        stale = True
    return "", stale


def conflicts(node: dict, published: dict, number: int) -> bool:
    """GitHub reads UNKNOWN until it has recomputed a PR against its moved base, so for that read the
    PR keeps what the last publish said of it."""
    if node.get("mergeable") == "UNKNOWN":
        return published.get("number") == number and published.get("conflicts") is True
    return node.get("mergeable") == "CONFLICTING"


def current_pr(pr: dict, live: dict | None, published: dict, now: float) -> dict:
    """The lane's PR with GitHub's state of it now; as pr.json has it, with no verdict, when the read failed."""
    key = pr_key(pr) if pr else None
    node = live.get(key) if live and key else None
    if node is None:
        return pr
    verdict, stale = solei_verdict(node)
    return {
        **pr,
        "state": str(node.get("state") or ""),
        "verdict": verdict,
        "verdict_stale": stale,
        "conflicts": conflicts(node, published, pr["number"]),
        "checked_at": int(now),
    }


def published_jobs() -> dict[str, dict]:
    """Each lane's row as the last publish wrote it."""
    board = read_json(handoffs_dir() / JOBS_FILE, {})
    jobs = board.get("jobs") if isinstance(board, dict) else None
    if not isinstance(jobs, dict):
        return {}
    return {slug: row for slug, row in jobs.items() if isinstance(row, dict)}


def parent_slug(directory: Path) -> str:
    """The lane a stacked lane waits on to raise, named in its `after` file."""
    return (read_text(directory / AFTER_FILE) or "").strip()


def pane_state(session: str) -> str:
    """What the session's pane shows, by the pane reader: prompt for a permission dialog nobody
    on the box will answer. Empty when the reader is not installed or finds no such session."""
    result = run(["bash", str(handoffs_dir() / PANE_READER), "state", session])
    return result.stdout.strip() if result.returncode == 0 else ""


def quota_wait(directory: Path, panes: dict[str, set[int]], table: dict[int, Process], now: float) -> dict:
    """The launcher's quota-wait.json while it is fresh and the launcher that wrote it still runs in the
    lane's pane, or {}. The pid in it is reused once its launcher exits, like a router's."""
    path = directory / QUOTA_WAIT_FILE
    text = read_text(path)
    if text is None:
        return {}
    try:
        marker = json.loads(text)
    except ValueError as error:
        # The launcher replaces the file whole, so a marker that does not parse was not read.
        raise ReadFailed(QUOTA_WAIT_FILE) from error
    if not isinstance(marker, dict):
        return {}
    pid = as_int(marker.get("pid"))
    process = table.get(pid)
    if process is None:
        return {}
    written = file_mtime(path)
    if written is None or process.started > written:
        return {}
    if directory.name not in owners(pid, panes, table):
        return {}
    next_poll = as_int(marker.get("next_poll"))
    if now > next_poll + QUOTA_WAIT_SLACK_S:
        return {}
    walled = marker.get("walled")
    return {
        "since": as_int(marker.get("since")),
        "next_poll": next_poll,
        "walled": [pool for pool in ("claude", "codex") if isinstance(walled, list) and pool in walled],
    }


def as_epoch(value) -> float | None:
    """A reset as either snapshot writes it: an ISO string, or epoch seconds."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def frees_at(windows: list[tuple[object, object]], wall: float, now: float) -> float | None:
    """When a walled account frees: the last of its windows at the wall resets. None for an account
    under the wall, one with a window its router cannot read, or one whose wall names no reset ahead."""
    resets = []
    for used, reset in windows:
        if not isinstance(used, (int, float)):
            return None
        if used < wall:
            continue
        reset_at = as_epoch(reset)
        if reset_at is None or reset_at <= now:
            return None
        resets.append(reset_at)
    return max(resets) if resets else None


def claude_frees(now: float) -> list[float]:
    snapshot = read_json(home() / CLAUDE_SNAPSHOT, {})
    rows = snapshot.get("accounts") if isinstance(snapshot, dict) else None
    frees = []
    for account in rows.values() if isinstance(rows, dict) else []:
        if not isinstance(account, dict) or account.get("expired") or account.get("canceled"):
            continue
        five_hour, seven_day = (account.get(key) or {} for key in ("five_hour", "seven_day"))
        # The router never picks a stale row, so its reset frees nothing.
        if five_hour.get("stale") or seven_day.get("stale"):
            continue
        five_hour_used = five_hour.get("used_pct")
        # A polled account with no 5-hour limit reads null there; the router counts it as 0. A row no
        # poll has written is stale, so it never reaches here.
        if five_hour_used is None:
            five_hour_used = 0.0
        windows = [(five_hour_used, five_hour.get("resets_at")), (seven_day.get("used_pct"), seven_day.get("resets_at"))]
        free = frees_at(windows, accounts.HARD_WALL_PCT, now)
        if free is not None:
            frees.append(free)
    return frees


def codex_frees(now: float) -> list[float]:
    usage = read_json(home() / CODEX_USAGE, {})
    frees = []
    for account in usage.values() if isinstance(usage, dict) else []:
        limits = account.get("rate_limits") if isinstance(account, dict) else None
        if not isinstance(limits, dict) or account.get("error"):
            continue
        # A flag walls the account whatever its windows read, and says nothing of when it lifts.
        if limits.get("rate_limit_reached_type") or limits.get("spend_control_reached"):
            continue
        # The router never picks a row older than this, so its reset frees nothing.
        if now - as_int(account.get("fetched_at")) > codex_accounts.USAGE_MAX_AGE_S:
            continue
        windows = [limits.get(key) for key in ("primary", "secondary")]
        free = frees_at(
            [(w.get("used_percent"), w.get("resets_at")) for w in windows if isinstance(w, dict)],
            CODEX_WALL_PCT,
            now,
        )
        if free is not None:
            frees.append(free)
    return frees


def earliest_reset(walled: list[str], now: float) -> int:
    """When the first account in a walled pool frees, by the box's own snapshots; 0 when they name none."""
    frees = []
    if "claude" in walled:
        frees += claude_frees(now)
    if "codex" in walled:
        frees += codex_frees(now)
    return int(min(frees, default=0))


def recent_handoffs(router: dict, now: float) -> int:
    """The router's moves in the last hour when there are more than HANDOFF_CHURN_MAX, else 0."""
    times = router.get("handoff_times")
    if not isinstance(times, list):
        return 0
    recent = sum(1 for at in times if isinstance(at, (int, float)) and now - at <= HANDOFF_WINDOW_S)
    return recent if recent > HANDOFF_CHURN_MAX else 0


def session_state(router: dict, queued: bool, after: str, codex: bool, quota: dict) -> str:
    """A live tmux session's state. One left at a bare shell after its router exited is not running."""
    if router.get("held_until"):
        return "held"
    # A gated lane's pane sits in the gate's sleep loop, with no router or codex yet.
    if queued:
        return "queued"
    if not router and not codex:
        # A lane's launcher sleeps at the quota wall, and a stacked lane's pane runs a waiter until
        # its parent raises; either resumes by itself.
        return "waiting" if quota or after else "gone"
    return "running"


def read_machine() -> Machine:
    failed: set[str] = set()
    panes = attempt(failed, "tmux", pane_pids, {})
    table = attempt(failed, "processes", processes, {})
    routers, unread = attempt(failed, "routers", lambda: routers_by_session(panes, table), ({}, set()))
    queue = attempt(failed, "queue", queued_slugs, [])
    return Machine(queue, panes, table, routers, unread, codex_sessions(panes, table), failed)


def carried(previous: dict, now: float, failed: set[str], ruled_out: frozenset[str] = frozenset()) -> dict:
    """The lane's last published row, stale since its first publish whose reads failed; its state is
    `unknown` when there is none, or when a read that answered rules it out."""
    state = str(previous.get("state") or "unknown")
    return {
        **previous,
        "state": "unknown" if state in ruled_out else state,
        "pr": previous["pr"] if isinstance(previous.get("pr"), dict) else {},
        "stale_since": as_int(previous.get("stale_since")) or int(now),
        "reads_failed": sorted(failed),
    }


def job_row(directory: Path, machine: Machine, previous: dict, now: float) -> dict | None:
    """The lane's row. When a read its state depends on fails, its state and what goes with it are the
    last publish's, or `unknown`; a failed read never makes a lane `gone`."""
    slug = directory.name
    failed: set[str] = set()
    text = attempt(failed, "job", lambda: read_text(directory / JOB_FILE), None)
    if failed:
        return carried(previous, now, failed)
    try:
        job = json.loads(text) if text is not None else None
    except ValueError:
        return None
    if not isinstance(job, dict):
        return None
    worktree = str(job.get("worktree") or "")
    report = attempt(failed, "report", lambda: report_status(directory / REPORT_FILE), None)
    after = attempt(failed, "after", lambda: parent_slug(directory), "")
    quota = attempt(failed, "quota", lambda: quota_wait(directory, machine.panes, machine.table, now), {})
    if slug in machine.unread_routers:
        failed.add("router")
    router = machine.routers.get(slug, {})
    queued = slug in machine.queue
    # None until a read settles it: a report that could not be read leaves it there.
    state = report
    if report is None and "report" not in failed:
        if slug not in machine.panes and "tmux" not in machine.failed:
            state = "gone"
        else:
            failed |= machine.failed
            if not failed:
                state = session_state(router, queued, after, slug in machine.codex, quota)
            if state == "running":
                pane = attempt(failed, "pane", lambda: pane_state(slug), "")
                state = None if failed else "prompt" if pane == "prompt" else state
    if state == "waiting" and quota:
        quota["resumes_at"] = earliest_reset(quota["walled"], now)
    else:
        quota = {}
    session = session_dir(str(router.get("session_id") or ""))
    row = {
        "state": state,
        "stale_since": 0,
        "reads_failed": [],
        "held_until": as_int(router.get("held_until")),
        "queue_position": machine.queue.index(slug) + 1 if queued else 0,
        "after": after,
        "quota": quota,
        "branch": str(job.get("branch") or ""),
        "head": attempt(failed, "git", lambda: git_head(worktree), previous.get("head", "")),
        "unpushed": attempt(failed, "git", lambda: unpushed_commits(worktree), previous.get("unpushed", 0)),
        "account": str(router.get("label") or ""),
        "origin_session": str(job.get("origin_session") or ""),
        "origin_pane": str(job.get("origin_pane") or ""),
        "origin_lane": str(job.get("origin_lane") or ""),
        "sent_at": as_int(job.get("sent_at")),
        "updated_at": last_change(directory, session),
        "handoffs": as_int(router.get("handoffs")),
        "handoffs_last_hour": recent_handoffs(router, now),
        "report": report is not None,
        "workflows": workflows_for(session, state in ("running", "prompt")),
        "messages": lane_messages(directory / OUTBOX_FILE),
        "pr": {},
    }
    row["reads_failed"] = sorted(failed)
    if state is None:
        # A read that answered still holds: a session tmux listed has not ended, and a lane with no
        # report has not reported.
        ruled_out = set() if "report" in failed else {"done", "blocked", "stopped"}
        if "tmux" not in machine.failed:
            ruled_out.add("gone")
        stale = carried(previous, now, failed, frozenset(ruled_out))
        row.update({field: stale[field] for field in (*CARRIED_FIELDS, "stale_since") if field in stale})
    return row


def build(now: float) -> dict:
    previous = published_jobs()
    jobs = {}
    unlisted = False
    try:
        directories = sorted(path for path in handoffs_dir().iterdir() if path.is_dir())
    except FileNotFoundError:
        directories = []
    except OSError:
        # With no list of its lanes, the box says nothing of any: each keeps its last row.
        unlisted, directories = True, []
        jobs = {slug: carried(row, now, {"handoffs"}) for slug, row in previous.items()}
    # GitHub first, while the budget is whole: a PR read it skipped would drop every verdict.
    prs = {directory.name: lane_pr(directory / PR_FILE) for directory in directories}
    live = read_prs({key for pr in prs.values() if (key := pr_key(pr))})
    machine = read_machine()
    if unlisted:
        machine.failed.add("handoffs")
    if live is None:
        machine.failed.add("github")
    for directory in directories:
        row = job_row(directory, machine, previous.get(directory.name, {}), now)
        if row is not None:
            jobs[directory.name] = row
    for slug, row in jobs.items():
        published = previous.get(slug, {}).get("pr")
        pr = prs.get(slug, row["pr"])
        row["pr"] = current_pr(pr, live, published if isinstance(published, dict) else {}, now)
    return {"version": 1, "generated_at": int(now), "reads_failed": sorted(machine.failed), "jobs": jobs}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp"
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def main() -> int:
    global deadline
    deadline = time.monotonic() + PUBLISH_BUDGET_S
    write_atomic(handoffs_dir() / JOBS_FILE, build(time.time()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
