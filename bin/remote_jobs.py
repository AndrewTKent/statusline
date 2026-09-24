#!/usr/bin/env python3
"""Publish what this machine's unattended Claude jobs are doing, for a laptop to pull.

A job is one tmux session working out of `~/handoffs/<slug>/`. Everything here is
derived from what is observable on this machine — the tmux session list, the
report the job wrote, the git HEAD of its worktree, the router's state file and
Claude Code's workflow journals — so a wedged session cannot report itself
healthy. The file is replaced atomically: a reader sees one version or the next.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

JOBS_FILE = "jobs.json"
JOB_FILE = "job.json"
REPORT_FILE = "report.md"
SENT_FILES = {"brief.md", "launch.sh"}
ROUTER_STATE_GLOB = "account-router-*.json"
ROUTER_PID = re.compile(r"account-router-(\d+)\.json$")
WORKFLOW_NAME = re.compile(r"name:\s*['\"]([^'\"]+)['\"]")
SCRIPT_HEAD_BYTES = 2048
COMMAND_TIMEOUT_S = 5


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def handoffs_dir() -> Path:
    return Path(os.environ.get("HANDOFFS_DIR") or home() / "handoffs")


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


def live_sessions() -> set[str]:
    return set(run(["tmux", "list-sessions", "-F", "#{session_name}"]).split())


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


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def router_alive(path: Path) -> bool:
    """A state file outlives a router that was killed, so the pid in its name decides."""
    match = ROUTER_PID.search(path.name)
    return bool(match) and pid_alive(int(match.group(1)))


def router_state_for(worktree: str) -> dict:
    """The live routed session working in this job's worktree, found by the cwd it renders."""
    if not worktree:
        return {}
    prefix = worktree.rstrip("/") + "/"
    for path in children(router_state_dir()):
        if not path.match(ROUTER_STATE_GLOB) or not router_alive(path):
            continue
        state = read_json(path, {})
        if not isinstance(state, dict):
            continue
        cwd = str(state.get("cwd") or "")
        if cwd == worktree or cwd.startswith(prefix):
            return state
    return {}


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


def codex_in(worktree: str) -> bool:
    """Codex runs unrouted, so a live codex process working in the worktree stands in for a router."""
    prefix = worktree.rstrip("/") + "/"
    for process in children(Path("/proc")):
        try:
            if (process / "comm").read_text().strip() != "codex":
                continue
            cwd = os.readlink(process / "cwd")
        except OSError:
            continue
        if cwd == worktree or cwd.startswith(prefix):
            return True
    return False


def session_state(tmux_alive: bool, worktree: str, router: dict) -> str:
    """A tmux session left at a bare shell after its router exited is not running.
    A job naming no worktree cannot be matched to a router, so tmux is all there is."""
    if not tmux_alive:
        return "gone"
    if router.get("held_until"):
        return "held"
    if worktree and not router and not codex_in(worktree):
        return "gone"
    return "running"


def job_row(directory: Path, live: set[str]) -> dict | None:
    job = read_json(directory / JOB_FILE, None)
    if not isinstance(job, dict):
        return None
    worktree = str(job.get("worktree") or "")
    report = report_status(directory / REPORT_FILE)
    router = router_state_for(worktree)
    state = report or session_state(directory.name in live, worktree, router)
    session = session_dir(str(router.get("session_id") or ""))
    return {
        "state": state,
        "held_until": as_int(router.get("held_until")),
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
    }


def build(now: float) -> dict:
    live = live_sessions()
    jobs = {}
    for directory in children(handoffs_dir()):
        if not directory.is_dir():
            continue
        row = job_row(directory, live)
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
