"""Pull another machine's account board to this one — percentages, never credentials.

A board is a machine that runs this router and polls its own accounts, writing
the same two snapshot files this machine writes. The poller here copies exactly
those two files over SSH into ~/.accounts/remote/<name>/ and records how the
pull went. Every renderer reads only those local copies, so nothing on the
render path ever touches the network.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
REMOTE_ROOT = HOME / ".accounts" / "remote"

CLAUDE_SNAPSHOT = "statusline-snapshot.json"
CODEX_USAGE = "codex-usage.json"
JOBS = "jobs.json"
META = "meta.json"

# The whole contract with a board: these three files, nothing else.
PULLED_FILES = (
    (CLAUDE_SNAPSHOT, "$HOME/.accounts/statusline-snapshot.json"),
    (CODEX_USAGE, "$HOME/.codex-accounts/usage.json"),
    (JOBS, "$HOME/handoffs/jobs.json"),
)

BOARD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "blob", "apikey", "email")
MAX_STRING_LEN = 512
CONNECT_TIMEOUT_S = 5
FETCH_TIMEOUT_S = 25
UP_CHECK_TIMEOUT_S = 20
DEFAULT_PULL_INTERVAL_S = 120.0
RUNNING_JOB_PULL_INTERVAL_S = 30.0


@dataclass(frozen=True)
class Board:
    name: str
    host: str
    up_command: str = ""


def up_command_var(name: str) -> str:
    return "REMOTE_BOARD_UP_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def parse_boards(raw: str, conf_var) -> list[Board]:
    boards = []
    for item in raw.split():
        name, separator, host = item.partition(":")
        if not separator or not host or not BOARD_NAME.match(name):
            continue
        boards.append(Board(name, host, conf_var(up_command_var(name)).strip()))
    return boards


def fetch_command() -> str:
    """One remote shell line: base64 of each pulled file, one per line, empty when absent."""
    paths = " ".join(f'"{remote}"' for _local, remote in PULLED_FILES)
    return (
        f"for board_file in {paths}; do "
        'if [ -r "$board_file" ]; then base64 < "$board_file" | tr -d "\\n"; fi; echo; done'
    )


def ssh_argv(host: str, command: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={CONNECT_TIMEOUT_S}",
        host,
        command,
    ]


def run_command(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _is_secret_key(key: str) -> bool:
    flat = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(part in flat for part in SECRET_KEY_PARTS)


def scrub(value):
    """Drop anything a board has no business publishing before it is stored."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not _is_secret_key(k)}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_STRING_LEN:
        return ""
    return value


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json_0600(path: Path, payload) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp"
    with open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def decode_payload(stdout: str) -> list[dict | None]:
    """One decoded JSON document per pulled file; None where the board had none."""
    lines = stdout.split("\n")
    documents: list[dict | None] = []
    for index in range(len(PULLED_FILES)):
        encoded = lines[index].strip() if index < len(lines) else ""
        if not encoded:
            documents.append(None)
            continue
        try:
            document = json.loads(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("bad payload") from exc
        documents.append(scrub(document))
    return documents


def board_up(board: Board, runner) -> bool | None:
    """None when the board declares no up-check — then a pull is the only probe."""
    if not board.up_command:
        return None
    try:
        # Login shell: the check is usually the machine's own CLI, off the user's PATH.
        result = runner(["bash", "-lc", board.up_command], UP_CHECK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0


def refresh_board(board: Board, *, now: float, runner) -> dict:
    directory = REMOTE_ROOT / board.name
    meta = read_json(directory / META, {})
    if not isinstance(meta, dict):
        meta = {}
    meta.update({"name": board.name, "attempted_at": now})
    up = board_up(board, runner)
    meta["up"] = up
    if up is False:
        meta["error"] = None
        write_json_0600(directory / META, meta)
        return meta

    try:
        result = runner(ssh_argv(board.host, fetch_command()), FETCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _record_error(directory, meta, "timeout")
    except OSError as exc:
        return _record_error(directory, meta, type(exc).__name__)
    if result.returncode != 0:
        return _record_error(directory, meta, f"ssh exit {result.returncode}")
    try:
        documents = decode_payload(result.stdout)
    except ValueError as exc:
        return _record_error(directory, meta, str(exc))

    for (local_name, _remote), document in zip(PULLED_FILES, documents):
        if document is None:
            continue
        write_json_0600(directory / local_name, document)
    meta.update({"fetched_at": now, "error": None})
    write_json_0600(directory / META, meta)
    return meta


def _record_error(directory: Path, meta: dict, error: str) -> dict:
    meta["error"] = error
    write_json_0600(directory / META, meta)
    return meta


def prune(names: set[str]) -> None:
    try:
        entries = list(REMOTE_ROOT.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name not in names and entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)


def has_running_job(name: str) -> bool:
    document = read_json(REMOTE_ROOT / name / JOBS, {})
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        return False
    return any(
        isinstance(job, dict) and job.get("state") == "running" for job in jobs.values()
    )


def refresh_all(conf_var, *, now: float | None = None, runner=None) -> list[dict]:
    boards = parse_boards(conf_var("REMOTE_ACCOUNT_BOARDS"), conf_var)
    prune({board.name for board in boards})
    if not boards:
        return []
    now = time.time() if now is None else now
    runner = runner or run_command
    try:
        interval = float(conf_var("REMOTE_BOARD_PULL_INTERVAL") or DEFAULT_PULL_INTERVAL_S)
    except ValueError:
        interval = DEFAULT_PULL_INTERVAL_S
    results = []
    for board in boards:
        previous = read_json(REMOTE_ROOT / board.name / META, {})
        attempted_at = previous.get("attempted_at") if isinstance(previous, dict) else 0
        # A board running a job is worth watching at the job's pace, not the board's.
        board_interval = interval
        if has_running_job(board.name):
            board_interval = min(interval, RUNNING_JOB_PULL_INTERVAL_S)
        try:
            due = now - float(attempted_at or 0) >= board_interval
        except (TypeError, ValueError):
            due = True
        if due:
            results.append(refresh_board(board, now=now, runner=runner))
    return results
