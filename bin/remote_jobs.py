#!/usr/bin/env python3
"""Publish what this machine's unattended Claude jobs are doing, for a laptop to pull.

A job is one tmux session working out of `~/handoffs/<slug>/`. Everything here is
derived from what is observable on this machine — the tmux panes and the
process tree under each one, the report the job wrote, the git HEAD
of its worktree, the router's state file and Claude Code's workflow journals —
so a wedged session cannot report itself healthy. The file is replaced
atomically: a reader sees one version or the next.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

JOBS_FILE = "jobs.json"
JOB_FILE = "job.json"
REPORT_FILE = "report.md"
QUEUE_FILE = "lane-queue.txt"
AFTER_FILE = "after"
OUTBOX_FILE = "outbox.jsonl"
PR_FILE = "pr.json"
PR_STATES = {"OPEN", "MERGED", "CLOSED"}
VERDICTS = {"☀️", "🌤️", "⛅", "🌧️"}
MESSAGES_KEPT = 20
MESSAGE_TEXT_MAX = 1000
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
SENT_FILES = {"brief.md", "launch.sh"}
ROUTER_STATE = re.compile(r"account-router-(\d+)\.json")
WORKFLOW_NAME = re.compile(r"name:\s*['\"]([^'\"]+)['\"]")
SCRIPT_HEAD_BYTES = 2048
COMMAND_TIMEOUT_S = 5
PROC = Path("/proc")
STAT_STARTTIME = 19  # field 22 of /proc/<pid>/stat, counted after the ")" that ends field 2


class Process(NamedTuple):
    parent: int
    command: str
    started: float


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def handoffs_dir() -> Path:
    return Path(os.environ.get("HANDOFFS_DIR") or home() / "handoffs")


def queued_slugs() -> list[str]:
    """Lanes the box's admission gate is holding, first in line first."""
    try:
        lines = (handoffs_dir() / QUEUE_FILE).read_text().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()]


def router_state_dir() -> Path:
    return Path(os.environ.get("ACCOUNTS_ROUTER_STATE_DIR") or "/tmp/claude")


def config_roots() -> list[Path]:
    """Every Claude Code config dir a job could be running under, routed or not."""
    raw = os.environ.get("CLAUDE_CONFIG_ROOTS")
    if raw:
        return [Path(part) for part in raw.split(":") if part]
    return [home() / ".claude", *sorted((home() / ".accounts" / "profiles").glob("*"))]


def run(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def pane_pids() -> dict[str, set[int]]:
    """The pid of the shell in each pane, by the tmux session it belongs to."""
    panes: dict[str, set[int]] = {}
    for line in run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"]).splitlines():
        pid, _, session = line.partition(" ")
        panes.setdefault(session, set()).add(int(pid))
    return panes


def boot_time() -> float:
    lines = (PROC / "stat").read_text().splitlines()
    return next(float(line.split()[1]) for line in lines if line.startswith("btime "))


def processes() -> dict[int, Process]:
    """Every process on this machine, by pid."""
    boot = boot_time()
    ticks = os.sysconf("SC_CLK_TCK")
    table = {}
    for process in children(PROC):
        if not process.name.isdigit():
            continue
        try:
            stat = (process / "stat").read_text()
        except OSError:
            continue
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


def as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def git_head(worktree: str) -> str:
    if not worktree:
        return ""
    return run(["git", "-C", worktree, "rev-parse", "--short", "HEAD"]).strip()


def report_status(path: Path) -> str | None:
    """None when the job wrote no report; otherwise done unless it says blocked."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    first = lines[0].strip().lower() if lines else ""
    return "blocked" if first == "status: blocked" else "done"


def live_router_pid(path: Path, table: dict[int, Process]) -> int:
    """A state file outlives its router and the pid in its name is reused, so the file is live
    only while that pid runs a process started before the file was last written; 0 otherwise."""
    match = ROUTER_STATE.fullmatch(path.name)
    process = table.get(int(match.group(1))) if match else None
    if process is None or process.started > mtime(path):
        return 0
    return int(match.group(1))


def routers_by_session(panes: dict[str, set[int]], table: dict[int, Process]) -> dict[str, dict]:
    """Each live router's state, by the tmux session it runs under, wherever it has cd'd to.
    Every lane runs in a session named for its slug, so a router under no pane is no job's."""
    routers: dict[str, dict] = {}
    for path in children(router_state_dir()):
        pid = live_router_pid(path, table)
        if not pid:
            continue
        state = read_json(path, {})
        if not isinstance(state, dict):
            continue
        for session in owners(pid, panes, table):
            routers.setdefault(session, state)
    return routers


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
    """The lane's newest PR as the box's lane-retire timer last read it from GitHub, or {}."""
    data = read_json(path, {})
    if not isinstance(data, dict) or as_int(data.get("number")) <= 0:
        return {}
    state = str(data.get("state") or "")
    verdict = str(data.get("verdict") or "")
    return {
        "number": as_int(data.get("number")),
        "state": state if state in PR_STATES else "",
        "url": str(data.get("url") or ""),
        "verdict": verdict if verdict in VERDICTS else "",
        "checked_at": as_int(data.get("checked_at")),
    }


def parent_slug(directory: Path) -> str:
    """The lane a stacked lane waits on to raise, named in its `after` file."""
    try:
        return (directory / AFTER_FILE).read_text().strip()
    except OSError:
        return ""


def session_state(tmux_alive: bool, router: dict, queued: bool, after: str, codex: bool) -> str:
    """A tmux session left at a bare shell after its router exited is not running."""
    if not tmux_alive:
        return "gone"
    if router.get("held_until"):
        return "held"
    # A gated lane's pane sits in the gate's sleep loop, with no router or codex yet.
    if queued:
        return "queued"
    if not router and not codex:
        # A stacked lane's pane runs a waiter until its parent raises.
        return "waiting" if after else "gone"
    return "running"


def job_row(
    directory: Path,
    queue: list[str],
    sessions: set[str],
    routers: dict[str, dict],
    codex: set[str],
) -> dict | None:
    job = read_json(directory / JOB_FILE, None)
    if not isinstance(job, dict):
        return None
    worktree = str(job.get("worktree") or "")
    report = report_status(directory / REPORT_FILE)
    router = routers.get(directory.name, {})
    queued = directory.name in queue
    after = parent_slug(directory)
    state = report or session_state(
        directory.name in sessions, router, queued, after, directory.name in codex
    )
    session = session_dir(str(router.get("session_id") or ""))
    return {
        "state": state,
        "held_until": as_int(router.get("held_until")),
        "queue_position": queue.index(directory.name) + 1 if queued else 0,
        "after": after,
        "branch": str(job.get("branch") or ""),
        "head": git_head(worktree),
        "account": str(router.get("label") or ""),
        "origin_session": str(job.get("origin_session") or ""),
        "origin_pane": str(job.get("origin_pane") or ""),
        "sent_at": as_int(job.get("sent_at")),
        "updated_at": last_change(directory, session),
        "handoffs": as_int(router.get("handoffs")),
        "report": report is not None,
        "workflows": workflows_for(session, state == "running"),
        "messages": lane_messages(directory / OUTBOX_FILE),
        "pr": lane_pr(directory / PR_FILE),
    }


def build(now: float) -> dict:
    queue = queued_slugs()
    panes = pane_pids()
    table = processes()
    sessions = set(panes)
    routers = routers_by_session(panes, table)
    codex = codex_sessions(panes, table)
    jobs = {}
    for directory in children(handoffs_dir()):
        if not directory.is_dir():
            continue
        row = job_row(directory, queue, sessions, routers, codex)
        if row is not None:
            jobs[directory.name] = row
    return {"version": 1, "generated_at": int(now), "jobs": jobs}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp"
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def main() -> int:
    write_atomic(handoffs_dir() / JOBS_FILE, build(time.time()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
