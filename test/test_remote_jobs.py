"""Unit tests for bin/remote_jobs.py — no tmux, no git, no network."""

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import remote_jobs  # noqa: E402

RUN_ID = "wf_0123456789a"
PANES = {"demo": 4242, "other": 4343, "0": 4242}  # "0" is grouped with demo: same pane
PANE = PANES["demo"]
ROUTER = 7000
LAUNCHER = 7100


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "handoffs").mkdir(parents=True)
    (home / ".claude" / "projects" / "-w").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "stat").write_text("cpu 0 0 0 0\nbtime 0\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HANDOFFS_DIR", str(home / "handoffs"))
    monkeypatch.setenv("ACCOUNTS_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_ROOTS", str(home / ".claude"))
    monkeypatch.setattr(remote_jobs, "PROC", tmp_path / "proc")
    live(monkeypatch)
    return home


def answer(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def live(monkeypatch, *sessions):
    """tmux lists these sessions, each with one pane whose shell is its pid in PANES."""
    def run(argv):
        if argv[0] == "tmux":
            return answer("".join(f"{PANES[session]} {session}\n" for session in sessions))
        return answer("abc1234\n" if argv[0] == "git" else "")

    monkeypatch.setattr(remote_jobs, "run", run)


def also_run(monkeypatch, command, reply):
    """`command` gets `reply`, an answer or a ReadFailed to raise; every other command what it got before."""
    before = remote_jobs.run

    def run(argv):
        if argv[0] != command:
            return before(argv)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(remote_jobs, "run", run)


def pane_reader_says(monkeypatch, state):
    """tmux lists the demo lane, and the pane reader prints `state` for it."""
    live(monkeypatch, "demo")
    also_run(monkeypatch, "bash", answer(state + "\n"))


def write_process(box, pid, parent, command="bash", started=0.0):
    """A /proc/<pid>/stat whose fields after the name are state, parent and, at index 19, start."""
    process = box.parent / "proc" / str(pid)
    process.mkdir(parents=True)
    ticks = int(started * os.sysconf("SC_CLK_TCK"))
    fields = ["S", str(parent), *["0"] * 17, str(ticks), "0"]
    (process / "stat").write_text(f"{pid} ({command}) {' '.join(fields)}\n")


def write_job(box, slug="demo", worktree="/w"):
    directory = box / "handoffs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "job.json").write_text(
        json.dumps(
            {
                "slug": slug,
                "branch": "andrew/demo",
                "worktree": worktree,
                "origin_session": "",
                "sent_at": 1000,
            }
        )
    )
    return directory


def drop_worktree(directory):
    job = json.loads((directory / "job.json").read_text())
    del job["worktree"]
    (directory / "job.json").write_text(json.dumps(job))


def write_router_state(
    box, session_id, label="team-1", handoffs=0, cwd="/w", parent=PANE, name=None, **extra
):
    """A router running under `parent` (the demo pane by default), or a dead one for None."""
    if parent is not None:
        write_process(box, ROUTER, parent, "python3")
    state = box.parent / "state" / (name or f"account-router-{ROUTER}.json")
    state.write_text(
        json.dumps(
            {"session_id": session_id, "cwd": cwd, "label": label, "handoffs": handoffs, **extra}
        )
    )
    return state


def write_workflow(box, session_id, name, started, done=(), failed=()):
    session = box / ".claude" / "projects" / "-w" / session_id
    journal = session / "subagents" / "workflows" / RUN_ID / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    events = [{"type": "launched"}]
    events += [{"type": "started", "agentId": agent} for agent in started]
    events += [{"type": "result", "agentId": agent} for agent in done]
    events += [{"type": "failed", "agentId": agent} for agent in failed]
    journal.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    scripts = session / "workflows" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / f"slug-{RUN_ID}.js").write_text(
        "export const meta = {\n  name: '%s',\n}\n" % name
    )


def write_quota_wait(box, slug="demo", walled=("claude", "codex"), next_poll=2100):
    """The marker box/launch.lib.sh keeps while the lane's launcher sleeps for quota."""
    marker = box / "handoffs" / slug / "quota-wait.json"
    marker.write_text(
        json.dumps({"since": 1900, "next_poll": next_poll, "walled": list(walled), "pid": LAUNCHER})
    )
    return marker


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def write_account_snapshots(box, codex_shift):
    """The box's account snapshots at t=2000. The Claude account that frees first frees at 4700, the
    Codex one at 4200 plus codex_shift; every other row is one its router would not pick even after
    its reset, so the earliest reset skips it."""
    def window(used, reset, stale=False):
        return {"used_pct": used, "resets_at": iso(reset), "stale": stale, "observed_at": 1990.0}

    def codex_row(primary, secondary=None, fetched_at=1990, **extra):
        limits = {"primary": primary, "secondary": secondary}
        return {"fetched_at": fetched_at, "rate_limits": limits, **extra}

    def limit(used, reset):
        return {"used_percent": used, "resets_at": reset + codex_shift}

    claude = {
        # At the router's hard wall, short of 100%: it frees when that window resets.
        "frees-at-4800": {"five_hour": window(98.0, 4800), "seven_day": window(30.0, 9900)},
        # Both windows at the wall: it frees only when the later resets.
        "frees-at-9000": {"five_hour": window(100.0, 4000), "seven_day": window(100.0, 9000)},
        "expired": {"expired": True, "five_hour": window(100.0, 3000), "seven_day": window(0.0, 9900)},
        "canceled": {"canceled": True, "five_hour": window(100.0, 3100), "seven_day": window(0.0, 9900)},
        "stale": {"five_hour": window(100.0, 3200, stale=True), "seven_day": window(0.0, 9900, stale=True)},
        # Polled with no 5-hour limit: the router reads that window as 0, so the weekly reset, at its
        # hard wall short of 100%, frees it.
        "no-five-hour-limit": {"five_hour": window(None, 9900), "seven_day": window(98.0, 4700)},
        "weekly-unread": {"five_hour": window(100.0, 3400), "seven_day": window(None, 3400)},
        "reset-already-passed": {"five_hour": window(100.0, 1000), "seven_day": window(0.0, 9900)},
        "under-the-wall": {"five_hour": window(96.0, 2500), "seven_day": window(0.0, 2600)},
    }
    codex = {
        "frees-at-4500": codex_row(limit(100, 4500)),
        "frees-at-4200": codex_row(limit(50, 9800), limit(100, 4200)),
        "stale": codex_row(limit(100, 4100), fetched_at=1000),
        "errored": codex_row(limit(100, 4000), error="usage read failed"),
        "flagged": codex_row(limit(100, 4050)),
        "spend-controlled": codex_row(limit(100, 4150)),
    }
    codex["flagged"]["rate_limits"]["rate_limit_reached_type"] = "primary"
    codex["spend-controlled"]["rate_limits"]["spend_control_reached"] = True
    (box / ".accounts").mkdir(parents=True, exist_ok=True)
    (box / ".accounts" / "statusline-snapshot.json").write_text(json.dumps({"accounts": claude}))
    (box / ".codex-accounts").mkdir(parents=True, exist_ok=True)
    (box / ".codex-accounts" / "usage.json").write_text(json.dumps(codex))


def only_job(payload):
    return payload["jobs"]["demo"]


class TestState:
    @pytest.mark.parametrize("command,session,listed,expected", [
        ("codex", "demo", ("demo", "other"), "running"),
        ("codex", "demo", ("0", "demo"), "running"),
        ("bash", "demo", ("demo", "other"), "gone"),
        ("codex", "other", ("demo", "other"), "gone"),
    ])
    def test_a_codex_job_is_running_while_codex_runs_under_its_pane(
        self, box, monkeypatch, command, session, listed, expected
    ):
        write_job(box)
        # codex-router, then the node launcher, then the codex binary.
        write_process(box, 100, PANES[session], "python3")
        write_process(box, 101, 100, "node")
        write_process(box, 102, 101, command)
        live(monkeypatch, *listed)

        assert only_job(remote_jobs.build(2000.0))["state"] == expected

    def test_a_router_under_the_lanes_pane_is_its_router_from_any_directory(
        self, box, monkeypatch
    ):
        write_job(box)
        write_router_state(box, "s-1", label="team-2", handoffs=3, cwd="/other-repo")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["account"], row["handoffs"]) == ("running", "team-2", 3)

    def test_a_router_under_another_lanes_pane_is_not_this_lanes_even_in_its_worktree(
        self, box, monkeypatch
    ):
        write_job(box)
        write_job(box, "other", "/o")
        write_router_state(box, "s-1", label="team-2", parent=PANES["other"])
        live(monkeypatch, "demo", "other")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["account"]) == ("gone", "")

    def test_a_lane_keeps_its_router_when_another_session_is_grouped_with_it(self, box, monkeypatch):
        write_job(box)
        write_router_state(box, "s-1")
        live(monkeypatch, "0", "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "running"

    def test_a_job_with_no_session_and_no_report_is_gone(self, box, monkeypatch):
        write_job(box)
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "gone"

    def test_a_job_naming_no_worktree_is_gone_once_its_router_exits(self, box, monkeypatch):
        drop_worktree(write_job(box))
        write_router_state(box, "s-1", parent=None)
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "gone"

    def test_a_router_in_the_worktree_under_no_pane_does_not_keep_the_lane_alive(
        self, box, monkeypatch
    ):
        write_job(box)
        write_router_state(box, "s-1", parent=1)
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "gone"

    def test_a_lane_the_gate_holds_publishes_queued_and_its_place(self, box, monkeypatch):
        write_job(box)
        (box / "handoffs" / "lane-queue.txt").write_text("other\ndemo\n")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["queue_position"]) == ("queued", 2)

    def test_a_router_holding_for_a_reset_publishes_held_and_when(self, box, monkeypatch):
        write_job(box)
        write_router_state(box, "s-1", held_until=5000, held_reason="session limit")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["held_until"]) == ("held", 5000)

    def test_a_stacked_lane_running_only_its_waiter_publishes_waiting_and_its_parent(
        self, box, monkeypatch
    ):
        directory = write_job(box)
        (directory / "after").write_text("iri-8386\n")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["after"]) == ("waiting", "iri-8386")

    @pytest.mark.parametrize("walled,codex_shift,resumes_at", [
        (["claude", "codex"], None, 0),
        (["claude"], 0, 4700),
        (["claude", "codex"], 0, 4200),
        (["codex"], 2000, 6200),
    ])
    def test_a_lane_whose_launcher_sleeps_at_the_quota_wall_publishes_waiting_and_when_it_resumes(
        self, box, monkeypatch, walled, codex_shift, resumes_at
    ):
        write_job(box)
        write_quota_wait(box, walled=walled)
        write_process(box, LAUNCHER, PANE)
        live(monkeypatch, "demo")
        if codex_shift is not None:
            write_account_snapshots(box, codex_shift)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["quota"]) == (
            "waiting",
            {"since": 1900, "next_poll": 2100, "walled": walled, "resumes_at": resumes_at},
        )

    @pytest.mark.parametrize("walled,published", [(None, []), (["gpu", "claude"], ["claude"])])
    def test_a_marker_names_only_the_pools_it_knows(self, box, walled, published):
        directory = write_job(box)
        marker = write_quota_wait(box)
        marker.write_text(json.dumps({**json.loads(marker.read_text()), "walled": walled}))
        write_process(box, LAUNCHER, PANE)

        wait = remote_jobs.quota_wait(directory, {"demo": {PANE}}, remote_jobs.processes(), 2000.0)

        assert wait["walled"] == published

    @pytest.mark.parametrize("case", ["stale", "launcher exited", "another lane's pane", "pid reused"])
    def test_a_quota_wait_marker_without_its_launcher_live_in_the_lanes_pane_is_gone(
        self, box, monkeypatch, case
    ):
        write_job(box)
        write_job(box, "other", "/o")
        marker = write_quota_wait(box, next_poll=1700 if case == "stale" else 2100)
        os.utime(marker, (1000, 1000))
        if case != "launcher exited":
            parent = PANES["other"] if case == "another lane's pane" else PANE
            write_process(box, LAUNCHER, parent, started=1500.0 if case == "pid reused" else 0.0)
        live(monkeypatch, "demo", "other")

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["quota"]) == ("gone", {})

    def test_a_lane_that_reported_while_its_launcher_slept_at_the_wall_is_done_with_no_wait(
        self, box, monkeypatch
    ):
        directory = write_job(box)
        write_quota_wait(box)
        write_process(box, LAUNCHER, PANE)
        (directory / "report.md").write_text("status: done\n")
        live(monkeypatch, "demo")

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["quota"]) == ("done", {})

    @pytest.mark.parametrize("pane,expected", [("prompt", "prompt"), ("busy", "running")])
    def test_a_running_lane_whose_pane_reader_sees_a_dialog_publishes_prompt(
        self, box, monkeypatch, pane, expected
    ):
        write_job(box)
        write_process(box, 102, PANE, "codex")
        live(monkeypatch, "demo")
        tmux_and_git = remote_jobs.run

        def run(argv):
            if argv == ["bash", str(box / "handoffs" / "lane-pane.sh"), "state", "demo"]:
                return answer(pane + "\n")
            return tmux_and_git(argv)

        monkeypatch.setattr(remote_jobs, "run", run)

        assert only_job(remote_jobs.build(2000.0))["state"] == expected

    def test_a_report_wins_over_a_dialog_still_on_its_pane(self, box, monkeypatch):
        directory = write_job(box)
        (directory / "report.md").write_text("status: done\n")
        write_process(box, 102, PANE, "codex")
        pane_reader_says(monkeypatch, "prompt")

        assert only_job(remote_jobs.build(2000.0))["state"] == "done"

    def test_a_lane_at_a_prompt_keeps_its_workflows_running(self, box, monkeypatch):
        write_job(box)
        write_router_state(box, "s-1")
        write_workflow(box, "s-1", "review loop", started=["a1", "a2"], done=["a1"])
        pane_reader_says(monkeypatch, "prompt")

        assert [w["running"] for w in only_job(remote_jobs.build(2000.0))["workflows"]] == [True]

    def test_a_report_wins_over_a_session_that_is_still_alive(self, box, monkeypatch):
        directory = write_job(box)
        (directory / "report.md").write_text("status: done\nbranch pushed\n")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: "done")

        assert only_job(remote_jobs.build(2000.0))["state"] == "done"


SENT_AT = 1000  # write_job's
JUST_SENT = SENT_AT + 30
PAST_LAUNCH = SENT_AT + remote_jobs.LAUNCH_WINDOW_S


class TestStarting:
    def test_a_lane_just_sent_with_no_session_yet_is_starting(self, box):
        write_job(box)

        assert only_job(remote_jobs.build(JUST_SENT))["state"] == "starting"

    def test_a_lane_just_sent_whose_session_runs_nothing_yet_is_starting(self, box, monkeypatch):
        write_job(box)
        live(monkeypatch, "demo")

        assert only_job(remote_jobs.build(JUST_SENT))["state"] == "starting"

    def test_a_lane_with_no_session_past_the_launch_window_is_gone(self, box):
        write_job(box)

        assert only_job(remote_jobs.build(PAST_LAUNCH))["state"] == "gone"

    def test_a_lane_seen_with_its_session_that_then_has_none_is_gone_at_once(self, box, monkeypatch):
        write_job(box)
        live(monkeypatch, "demo")
        write_router_state(box, "s-1")
        remote_jobs.write_atomic(box / "handoffs" / "jobs.json", remote_jobs.build(SENT_AT + 5))
        live(monkeypatch)

        assert only_job(remote_jobs.build(JUST_SENT))["state"] == "gone"

    def test_a_lane_sent_again_has_not_started_until_a_session_is_listed_for_the_new_send(self, box):
        write_job(box)
        published(box, state="stopped", started=SENT_AT - 500)

        assert only_job(remote_jobs.build(JUST_SENT))["started"] == 0

    def test_a_failed_tmux_read_of_a_lane_just_sent_is_unknown(self, box, monkeypatch):
        write_job(box)
        fail_read(box, monkeypatch, "tmux")

        row = only_job(remote_jobs.build(JUST_SENT))

        assert (row["state"], row["reads_failed"]) == ("unknown", ["tmux"])


LAST_PUBLISH = {
    "state": "held", "held_until": 5000, "queue_position": 0, "after": "", "quota": {},
    "account": "team-2", "handoffs": 4, "handoffs_last_hour": 3, "stale_since": 0,
    "workflows": [{"name": "review loop", "running": True}], "head": "f00d123", "unpushed": 2,
}


def published(box, **row):
    """The demo lane as the last publish wrote it."""
    (box / "handoffs" / "jobs.json").write_text(json.dumps({"jobs": {"demo": {**LAST_PUBLISH, **row}}}))


def carried_part(row):
    """What a lane keeps of the last publish while its state cannot be read, and the marks saying so."""
    kept = ("state", "held_until", "queue_position", "after", "quota", "account", "handoffs",
            "handoffs_last_hour", "workflows", "stale_since", "reads_failed")
    return {field: row[field] for field in kept}


def fail_read(box, monkeypatch, read):
    """Make one read of the machine fail the way it does on a saturated or broken box."""
    directory = box / "handoffs" / "demo"
    if read == "tmux":
        also_run(monkeypatch, "tmux", remote_jobs.ReadFailed("tmux"))
    elif read == "tmux exit":
        also_run(monkeypatch, "tmux", answer(code=1, stderr="error connecting to /tmp/tmux-1000/default (Permission denied)\n"))
    elif read == "processes":
        (box.parent / "proc" / "stat").unlink()
    elif read == "process stat":
        (box.parent / "proc" / "999" / "stat").mkdir(parents=True)
    elif read == "routers":
        (box.parent / "state").rmdir()
        (box.parent / "state").write_text("")
    elif read == "router":
        write_router_state(box, "s-1").write_text("{")
    elif read == "queue":
        (box / "handoffs" / "lane-queue.txt").mkdir()
    elif read == "quota":
        (directory / "quota-wait.json").write_text("{")
    elif read == "after":
        (directory / "after").mkdir()
    elif read == "report":
        (directory / "report.md").mkdir()
        # With no session listed, only the report could say this lane did not just end.
        live(monkeypatch)
    elif read in ("router stat", "quota stat"):
        if read == "router stat":
            write_router_state(box, "s-1")
        else:
            write_quota_wait(box)
            write_process(box, LAUNCHER, PANE)
        unreadable = "account-router-" if read == "router stat" else "quota-wait.json"
        file_mtime = remote_jobs.file_mtime

        def stat_fails(path):
            if path.name.startswith(unreadable):
                raise remote_jobs.ReadFailed(path.name)
            return file_mtime(path)

        monkeypatch.setattr(remote_jobs, "file_mtime", stat_fails)
    elif read == "pane":
        write_router_state(box, "s-1")
        also_run(monkeypatch, "bash", remote_jobs.ReadFailed("bash"))


def after_unreadable(monkeypatch):
    def unreadable(_directory):
        raise remote_jobs.ReadFailed("after")

    monkeypatch.setattr(remote_jobs, "parent_slug", unreadable)


class TestFailedReads:
    @pytest.mark.parametrize("read,failed", [
        ("tmux", "tmux"), ("tmux exit", "tmux"), ("processes", "processes"), ("process stat", "processes"),
        ("routers", "routers"),
        ("router", "router"), ("router stat", "router"), ("queue", "queue"), ("quota", "quota"),
        ("quota stat", "quota"), ("after", "after"),
        ("report", "report"), ("pane", "pane"),
    ])
    def test_a_read_that_failed_leaves_the_lane_as_the_last_publish_had_it_stale_from_now(
        self, box, monkeypatch, read, failed
    ):
        write_job(box)
        published(box)
        live(monkeypatch, "demo")
        fail_read(box, monkeypatch, read)

        row = only_job(remote_jobs.build(2000.0))

        assert carried_part(row) == {**carried_part({**LAST_PUBLISH, "reads_failed": []}),
                                     "stale_since": 2000, "reads_failed": [failed]}

    def test_a_lane_stays_stale_from_its_first_publish_whose_read_failed(self, box, monkeypatch):
        write_job(box)
        published(box, stale_since=1500)
        live(monkeypatch, "demo")
        after_unreadable(monkeypatch)

        assert only_job(remote_jobs.build(2000.0))["stale_since"] == 1500

    @pytest.mark.parametrize("last", ["gone", "done"])
    def test_a_last_state_a_read_that_answered_rules_out_is_unknown(self, box, monkeypatch, last):
        # tmux lists the lane, so it has not ended; it has no report, so it has not reported.
        write_job(box)
        published(box, state=last)
        live(monkeypatch, "demo")
        after_unreadable(monkeypatch)

        assert only_job(remote_jobs.build(2000.0))["state"] == "unknown"

    def test_a_lane_whose_reads_answer_again_is_no_longer_stale(self, box, monkeypatch):
        (write_job(box) / "after").write_text("iri-8386\n")
        published(box, stale_since=1500, reads_failed=["tmux"])
        live(monkeypatch, "demo")

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["stale_since"], row["reads_failed"]) == ("waiting", 0, [])

    @pytest.mark.parametrize("stderr", [
        "no server running on /tmp/tmux-1000/default\n",
        "error connecting to /tmp/tmux-1000/default (No such file or directory)\n",
    ])
    def test_tmux_with_no_server_lists_no_session_so_the_lane_is_gone(self, box, monkeypatch, stderr):
        write_job(box)
        published(box)
        also_run(monkeypatch, "tmux", answer(code=1, stderr=stderr))

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["stale_since"], row["reads_failed"]) == ("gone", 0, [])

    def test_a_lane_tmux_lists_no_session_for_is_gone_whatever_else_failed(self, box, monkeypatch):
        write_job(box)
        published(box)

        def unreadable():
            raise remote_jobs.ReadFailed("processes")

        monkeypatch.setattr(remote_jobs, "processes", unreadable)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["stale_since"]) == ("gone", 0)

    def test_a_report_is_published_while_tmux_cannot_be_read(self, box, monkeypatch):
        directory = write_job(box)
        (directory / "report.md").write_text("status: done\n")
        published(box)
        fail_read(box, monkeypatch, "tmux")

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["stale_since"]) == ("done", 0)

    @pytest.mark.parametrize("git", ["timed out", "failed in a worktree that is there"])
    def test_a_git_read_that_failed_keeps_the_last_head_and_unpushed_count(self, box, monkeypatch, git):
        write_job(box, worktree=str(box))
        published(box)
        reply = remote_jobs.ReadFailed("git") if git == "timed out" else answer(code=128, stderr="fatal: Out of memory\n")
        also_run(monkeypatch, "git", reply)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["head"], row["unpushed"], row["stale_since"]) == ("f00d123", 2, 0)

    def test_a_worktree_since_removed_has_no_head_and_nothing_unpushed(self, box, monkeypatch):
        write_job(box, worktree=str(box / "removed"))
        published(box)
        also_run(monkeypatch, "git", answer(code=128, stderr="fatal: cannot change to 'removed'\n"))

        row = only_job(remote_jobs.build(2000.0))

        assert (row["head"], row["unpushed"]) == ("", 0)

    @pytest.mark.parametrize("seen,state", [(True, "held"), (False, "unknown")])
    def test_a_job_file_that_could_not_be_read_keeps_the_lanes_last_row(self, box, monkeypatch, seen, state):
        write_job(box)
        if seen:
            published(box, branch="andrew/demo", pr={})
        read_text = remote_jobs.read_text

        def job_file_unreadable(path):
            if path.name == "job.json":
                raise remote_jobs.ReadFailed(path.name)
            return read_text(path)

        monkeypatch.setattr(remote_jobs, "read_text", job_file_unreadable)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row.get("branch"), row["stale_since"], row["reads_failed"]) == (
            state, "andrew/demo" if seen else None, 2000, ["job"]
        )

    @pytest.mark.parametrize("name,expected", [("missing", None), ("under-a-file/x", remote_jobs.ReadFailed)])
    def test_a_file_that_is_not_there_has_no_mtime_and_one_that_cannot_be_read_fails(self, tmp_path, name, expected):
        (tmp_path / "under-a-file").write_text("")
        if expected is None:
            assert remote_jobs.file_mtime(tmp_path / name) is None
        else:
            with pytest.raises(expected):
                remote_jobs.file_mtime(tmp_path / name)

    def test_a_box_with_no_router_state_dir_has_no_router_and_reads_fine(self, box, monkeypatch):
        (write_job(box) / "after").write_text("iri-8386\n")
        (box.parent / "state").rmdir()
        live(monkeypatch, "demo")

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["reads_failed"]) == ("waiting", [])

    def test_a_box_with_no_handoffs_dir_publishes_no_lanes_and_no_failed_read(self, box):
        (box / "handoffs").rmdir()

        board = remote_jobs.build(2000.0)

        assert (board["jobs"], board.get("reads_failed", [])) == ({}, [])

    def test_a_report_that_is_not_utf8_still_ends_the_lane(self, box):
        (write_job(box) / "report.md").write_bytes(b"status: blocked\n\xff\xfe\n")

        assert only_job(remote_jobs.build(2000.0))["state"] == "blocked"

    def test_a_handoffs_dir_that_could_not_be_listed_keeps_every_lane_and_says_so(self, box):
        published(box, pr={})
        handoffs = box / "handoffs"
        handoffs.chmod(0o300)
        try:
            board = remote_jobs.build(2000.0)
        finally:
            handoffs.chmod(0o700)

        assert (board["reads_failed"], board["jobs"]["demo"]["state"]) == (["handoffs"], "held")


class TestBudget:
    def test_a_publish_past_its_budget_starts_no_command(self, monkeypatch):
        started = []
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: started.append(args))
        monkeypatch.setattr(remote_jobs, "deadline", time.monotonic() - 1)

        with pytest.raises(remote_jobs.ReadFailed):
            remote_jobs.run(["tmux", "list-panes"])
        assert started == []

    def test_a_command_gets_no_more_than_the_budget_has_left(self, monkeypatch):
        timeouts = []
        monkeypatch.setattr(subprocess, "run", lambda *args, timeout, **kwargs: timeouts.append(timeout))
        monkeypatch.setattr(remote_jobs, "deadline", time.monotonic() + 1)

        remote_jobs.run(["tmux", "list-panes"])

        assert 0 < timeouts[0] <= 1

    def test_each_publish_gets_the_budget_from_when_it_starts(self, box, monkeypatch):
        budgets = []
        monkeypatch.setattr(remote_jobs, "deadline", math.inf)
        monkeypatch.setattr(remote_jobs, "build", lambda _now: budgets.append(remote_jobs.deadline - time.monotonic()) or {})

        remote_jobs.main()

        assert remote_jobs.PUBLISH_BUDGET_S - 1 < budgets[0] <= remote_jobs.PUBLISH_BUDGET_S


class TestReport:
    @pytest.mark.parametrize("first,state", [("status: blocked", "blocked"), ("Status: Stopped", "stopped")])
    def test_the_first_line_can_say_the_job_is_blocked_or_was_stopped(self, tmp_path, first, state):
        report = tmp_path / "report.md"
        report.write_text(f"{first}\nwhy\n")

        assert remote_jobs.report_status(report) == state

    def test_a_report_that_states_no_status_is_done(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("branch pushed\n")

        assert remote_jobs.report_status(report) == "done"

    def test_a_job_that_wrote_nothing_has_no_report(self, tmp_path):
        assert remote_jobs.report_status(tmp_path / "report.md") is None


class TestUpdatedAt:
    def test_a_rewritten_launcher_does_not_move_the_clock(self, box):
        directory = write_job(box)
        report = directory / "report.md"
        report.write_text("branch pushed\n")
        os.utime(report, (1500, 1500))
        os.utime(directory / "job.json", (1000, 1000))
        launcher = directory / "launch.sh"
        launcher.write_text("exec bash\n")
        os.utime(launcher, (1900, 1900))

        assert only_job(remote_jobs.build(2000.0))["updated_at"] == 1500


class TestWorktree:
    def test_a_lane_publishes_the_commits_its_worktree_holds_that_no_remote_does(self, box, monkeypatch):
        write_job(box)
        tmux_and_git = remote_jobs.run
        count = ["git", "-C", "/w", "rev-list", "--count", "HEAD", "--not", "--remotes"]
        monkeypatch.setattr(remote_jobs, "run", lambda argv: answer("3\n") if argv == count else tmux_and_git(argv))

        assert only_job(remote_jobs.build(2000.0))["unpushed"] == 3


class TestOrigin:
    def test_a_lane_publishes_the_name_of_the_lane_that_sent_it(self, box):
        directory = write_job(box)
        job = json.loads((directory / "job.json").read_text())
        (directory / "job.json").write_text(json.dumps({**job, "origin_lane": "infra"}))

        assert only_job(remote_jobs.build(2000.0))["origin_lane"] == "infra"


class TestRouterState:
    @pytest.mark.parametrize("times,published", [([9900, 9000, 7000], 3), ([9900, 9000, 6399], 0)])
    def test_a_lane_publishes_its_moves_in_the_last_hour_only_past_two(
        self, box, monkeypatch, times, published
    ):
        write_job(box)
        write_router_state(box, "s-1", handoffs=7, handoff_times=times)
        live(monkeypatch, "demo")

        assert only_job(remote_jobs.build(10000.0))["handoffs_last_hour"] == published

    def test_a_dead_routers_pid_reused_after_its_last_write_is_not_its_router(self, box):
        state = write_router_state(box, "s-1", parent=None)
        os.utime(state, (100, 100))
        table = {ROUTER: remote_jobs.Process(PANE, "python3", 500.0)}

        assert remote_jobs.live_router_pid(state, table) == 0

    def test_a_temp_file_beside_a_state_file_is_not_one(self, box, monkeypatch):
        write_job(box)
        write_router_state(box, "s-1", label="team-2", name=f"account-router-{ROUTER}.json.tmp.5")
        live(monkeypatch, "demo")

        assert only_job(remote_jobs.build(2000.0))["account"] == ""


def session_of(box, session_id):
    return box / ".claude" / "projects" / "-w" / session_id


class TestWorkflows:
    def test_agents_still_out_mean_the_workflow_is_running(self, box):
        write_workflow(box, "s-1", "review loop", started=("a", "b"), done=("a",))

        workflow = remote_jobs.workflows_for(session_of(box, "s-1"), True)[0]

        assert (workflow["running"], workflow["agents_done"], workflow["agents_started"]) == (
            True,
            1,
            2,
        )

    def test_agents_still_out_under_a_job_that_stopped_are_not_running(self, box):
        write_workflow(box, "s-1", "review loop", started=("a", "b"), done=("a",))

        assert remote_jobs.workflows_for(session_of(box, "s-1"), False)[0]["running"] is False

    def test_a_workflow_whose_agents_all_reported_is_not_running(self, box):
        write_workflow(box, "s-1", "review loop", started=("a",), failed=("a",))

        assert remote_jobs.workflows_for(session_of(box, "s-1"), True)[0]["running"] is False

    def test_the_workflow_is_named_by_its_script_rather_than_its_run_id(self, box):
        write_workflow(box, "s-1", "review loop", started=("a",))

        assert remote_jobs.workflows_for(session_of(box, "s-1"), True)[0]["name"] == (
            "review loop"
        )

    def test_a_job_lists_the_workflows_of_the_session_its_router_state_names(
        self, box, monkeypatch
    ):
        write_job(box)
        write_router_state(box, "s-1")
        write_workflow(box, "s-1", "review loop", started=("a",))
        live(monkeypatch, "demo")

        workflows = only_job(remote_jobs.build(2000.0))["workflows"]

        assert [row["agents_started"] for row in workflows] == [1]


class TestMessages:
    def write_outbox(self, box, *lines):
        directory = write_job(box)
        (directory / "outbox.jsonl").write_text("".join(line + "\n" for line in lines))

    def entry(self, message_id, text="hello", ts=1000):
        return json.dumps({"id": message_id, "ts": ts, "slug": "demo", "text": text})

    def messages(self):
        return remote_jobs.build(1)["jobs"]["demo"]["messages"]

    def test_a_lane_that_posted_nothing_has_no_messages(self, box):
        write_job(box)
        assert self.messages() == []

    def test_the_last_lines_are_published_oldest_first(self, box):
        self.write_outbox(box, *(self.entry(f"m{n}") for n in range(25)))
        assert [m["id"] for m in self.messages()] == [f"m{n}" for n in range(5, 25)]

    def test_a_line_that_is_not_json_is_skipped(self, box):
        self.write_outbox(box, "{half a line", self.entry("m1"))
        assert [m["id"] for m in self.messages()] == ["m1"]

    def test_an_id_that_could_name_a_path_is_dropped(self, box):
        self.write_outbox(box, self.entry("../../etc/passwd"), self.entry("m1"))
        assert [m["id"] for m in self.messages()] == ["m1"]

    def test_control_characters_in_the_text_become_spaces(self, box):
        self.write_outbox(box, self.entry("m1", "red\x1b[31m alert\x07\x9b31m"))
        assert self.messages()[0]["text"] == "red [31m alert  31m"

    def test_the_text_is_capped(self, box):
        self.write_outbox(box, self.entry("m1", "x" * 5000))
        assert len(self.messages()[0]["text"]) == remote_jobs.MESSAGE_TEXT_MAX


PR_URL = "https://github.com/o/r/pull/40"


def pr_json(directory, number=40, url=PR_URL, state="OPEN", verdict="🌧️"):
    """What agent-workflows' lane-retire timer writes for a lane's newest PR."""
    (directory / "pr.json").write_text(
        json.dumps({"number": number, "state": state, "url": url, "verdict": verdict, "checked_at": 5})
    )


def review(body, commit, at, login="claude"):
    return {"author": {"login": login}, "body": body, "commit": {"oid": commit}, "submittedAt": at}


def pull(head="h2", mergeable="MERGEABLE", state="OPEN", reviews=(), comments=()):
    return {"number": 40, "state": state, "url": PR_URL, "headRefOid": head, "mergeable": mergeable,
            "reviews": {"nodes": list(reviews)}, "comments": {"nodes": list(comments)}}


def github_has(monkeypatch, data, code=0):
    """`gh api graphql` answers with `data` and exits `code`; returns the queries it was asked."""
    queries = []
    tmux_and_git = remote_jobs.run

    def run(argv):
        if argv[0] != "gh":
            return tmux_and_git(argv)
        queries.append(argv)
        return answer(json.dumps({"data": data}), code) if data is not None else answer(code=1)

    monkeypatch.setattr(remote_jobs, "run", run)
    return queries


class TestPullRequest:
    def pr(self, box, monkeypatch, node, slug="demo"):
        pr_json(write_job(box, slug))
        github_has(monkeypatch, {"r0": {"p40": node}})
        return remote_jobs.build(2000.0)["jobs"][slug]["pr"]

    @pytest.mark.parametrize("posts", ["review", "comment"])
    def test_a_verdict_the_review_bot_gave_on_the_prs_head_is_published_with_githubs_state(
        self, box, monkeypatch, posts
    ):
        # A review names the commit it reviewed; a comment, posted when the review fails to, names it in its body.
        reviews = [review("🌧️ Changes.", "h1", "2026-11-14T07:00:00Z")]
        comments = []
        if posts == "review":
            reviews.append(review("☀️ Approve.", "h2", "2026-11-14T07:15:49Z"))
        else:
            comments.append({"author": {"login": "claude"}, "body": "\u2600 Approve. Reviewed h2.",
                             "createdAt": "2026-11-14T07:15:49Z"})

        pr = self.pr(box, monkeypatch, pull(state="MERGED", reviews=reviews, comments=comments))

        assert pr == {"number": 40, "state": "MERGED", "url": PR_URL, "verdict": "☀️",
                      "verdict_stale": False, "conflicts": False, "checked_at": 2000}

    def test_a_verdict_given_only_on_an_older_head_is_stale_and_shows_no_emoji(self, box, monkeypatch):
        pr = self.pr(box, monkeypatch, pull(reviews=[review("☀️ Approve.", "h1", "2026-11-14T07:15:49Z")]))

        assert (pr["verdict"], pr["verdict_stale"]) == ("", True)

    def test_the_newest_verdict_on_the_head_wins(self, box, monkeypatch):
        monkeypatch.setattr(remote_jobs, "on_head", lambda _post, _head: True)
        reviews = [review("🌤️ Approve.", "h2", "2026-11-14T07:00:00Z"), review("☀️ Approve.", "h2", "2026-11-14T07:15:00Z")]

        assert self.pr(box, monkeypatch, pull(reviews=reviews))["verdict"] == "☀️"

    def test_a_weather_emoji_from_anyone_but_the_review_bot_is_no_verdict(self, box, monkeypatch):
        pr = self.pr(box, monkeypatch, pull(reviews=[review("☀️ lgtm", "h2", "2026-11-14T07:15:00Z", "AndrewTKent")]))

        assert (pr["verdict"], pr["verdict_stale"]) == ("", False)

    def test_a_pr_that_conflicts_with_its_base_says_so(self, box, monkeypatch):
        assert self.pr(box, monkeypatch, pull(mergeable="CONFLICTING"))["conflicts"] is True

    @pytest.mark.parametrize("published,conflicts", [(40, True), (39, False)])
    def test_while_github_recomputes_a_pr_it_keeps_what_the_last_publish_said(
        self, box, monkeypatch, published, conflicts
    ):
        board = {"jobs": {"demo": {"pr": {"number": published, "conflicts": True}}}}
        (box / "handoffs" / "jobs.json").write_text(json.dumps(board))

        assert self.pr(box, monkeypatch, pull(mergeable="UNKNOWN"))["conflicts"] is conflicts

    def test_every_lanes_pr_is_read_in_one_query(self, box, monkeypatch):
        monkeypatch.setattr(remote_jobs, "on_head", lambda _post, _head: True)
        pr_json(write_job(box, "demo"))
        pr_json(write_job(box, "other", "/o"), number=7, url="https://github.com/o/s/pull/7")
        queries = github_has(monkeypatch, {
            "r0": {"p40": pull(reviews=[review("☀️", "h2", "1")])},
            "r1": {"p7": pull(reviews=[review("⛅", "h2", "1")])},
        })

        jobs = remote_jobs.build(2000.0)["jobs"]

        assert (len(queries), jobs["demo"]["pr"]["verdict"], jobs["other"]["pr"]["verdict"]) == (1, "☀️", "⛅")

    def test_no_lane_with_a_pr_means_no_github_call(self, box, monkeypatch):
        write_job(box)
        queries = github_has(monkeypatch, {})

        remote_jobs.build(2000.0)

        assert queries == []

    def test_a_failed_read_keeps_the_pr_as_its_file_has_it_with_no_verdict(self, box, monkeypatch):
        pr_json(write_job(box))
        # gh exits non-zero on a query GitHub answered only in part.
        github_has(monkeypatch, {"r0": {"p40": pull(state="MERGED")}}, code=1)

        board = remote_jobs.build(2000.0)

        assert (board["jobs"]["demo"]["pr"], board["reads_failed"]) == ({
            "number": 40, "state": "OPEN", "url": PR_URL, "verdict": "",
            "verdict_stale": False, "conflicts": False, "checked_at": 5,
        }, ["github"])

    def test_github_is_read_before_the_machine_while_the_budget_is_whole(self, box, monkeypatch):
        pr_json(write_job(box))
        github_has(monkeypatch, {"r0": {"p40": pull()}})
        commands = []
        before = remote_jobs.run
        monkeypatch.setattr(remote_jobs, "run", lambda argv: commands.append(argv[0]) or before(argv))

        remote_jobs.build(2000.0)

        assert commands[:2] == ["gh", "tmux"]

    def test_a_lane_with_no_pr_file_has_no_pr(self, box):
        write_job(box)

        assert only_job(remote_jobs.build(1))["pr"] == {}

    def test_a_state_that_is_not_githubs_is_dropped(self, tmp_path):
        path = tmp_path / "pr.json"
        path.write_text(json.dumps({"number": 40, "state": "\x1b[2J"}))

        assert remote_jobs.lane_pr(path)["state"] == ""


class TestProcesses:
    def test_the_table_holds_each_pids_parent_name_and_start_and_nothing_else(self, box):
        (box.parent / "proc" / "stat").write_text("cpu 0 0 0 0\nbtime 1000\n")
        write_process(box, 77, 5, "tmux: (server)", started=250)
        (box.parent / "proc" / "self").mkdir()
        (box.parent / "proc" / "self" / "stat").write_text("9 (bash) S 1 9 9 0 -1\n")
        (box.parent / "proc" / "88").mkdir()

        assert remote_jobs.processes() == {77: remote_jobs.Process(5, "tmux: (server)", 1250.0)}

    def test_a_process_name_that_is_not_utf8_is_read(self, box):
        (box.parent / "proc" / "77").mkdir()
        (box.parent / "proc" / "77" / "stat").write_bytes(b"77 (lane\xff\xfe) S 5 " + b"0 " * 17 + b"0 0\n")

        assert remote_jobs.processes()[77].parent == 5

    def test_a_parent_loop_from_a_reused_pid_is_not_descent(self):
        table = {10: remote_jobs.Process(11, "a", 0.0), 11: remote_jobs.Process(10, "b", 0.0)}

        assert remote_jobs.descends_from(10, {PANE}, table) is False


def test_a_directory_without_a_job_file_is_not_a_job(box):
    (box / "handoffs" / "scratch").mkdir()

    assert remote_jobs.build(2000.0)["jobs"] == {}
