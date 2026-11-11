"""Unit tests for bin/remote_jobs.py — no tmux, no git, no network."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import remote_jobs  # noqa: E402

RUN_ID = "wf_0123456789a"
PANES = {"demo": 4242, "other": 4343, "0": 4242}  # "0" is grouped with demo: same pane
PANE = PANES["demo"]
ROUTER = 7000


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


def live(monkeypatch, *sessions):
    """tmux lists these sessions, each with one pane whose shell is its pid in PANES."""
    def run(argv):
        if argv[0] == "tmux":
            return "".join(f"{PANES[session]} {session}\n" for session in sessions)
        return "abc1234\n"

    monkeypatch.setattr(remote_jobs, "run", run)


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

    def test_a_report_wins_over_a_session_that_is_still_alive(self, box, monkeypatch):
        directory = write_job(box)
        (directory / "report.md").write_text("status: done\nbranch pushed\n")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: "done")

        assert only_job(remote_jobs.build(2000.0))["state"] == "done"


class TestReport:
    def test_the_first_line_can_say_the_job_is_blocked(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("status: blocked\nneeds a decision\n")

        assert remote_jobs.report_status(report) == "blocked"

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


class TestRouterState:
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


class TestProcesses:
    def test_the_table_holds_each_pids_parent_name_and_start_and_nothing_else(self, box):
        (box.parent / "proc" / "stat").write_text("cpu 0 0 0 0\nbtime 1000\n")
        write_process(box, 77, 5, "tmux: (server)", started=250)
        (box.parent / "proc" / "self").mkdir()
        (box.parent / "proc" / "self" / "stat").write_text("9 (bash) S 1 9 9 0 -1\n")
        (box.parent / "proc" / "88").mkdir()

        assert remote_jobs.processes() == {77: remote_jobs.Process(5, "tmux: (server)", 1250.0)}

    def test_a_parent_loop_from_a_reused_pid_is_not_descent(self):
        table = {10: remote_jobs.Process(11, "a", 0.0), 11: remote_jobs.Process(10, "b", 0.0)}

        assert remote_jobs.descends_from(10, {PANE}, table) is False


def test_a_directory_without_a_job_file_is_not_a_job(box):
    (box / "handoffs" / "scratch").mkdir()

    assert remote_jobs.build(2000.0)["jobs"] == {}
