"""Unit tests for bin/remote_jobs.py — no tmux, no git, no network."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import remote_jobs  # noqa: E402

RUN_ID = "wf_0123456789a"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "handoffs").mkdir(parents=True)
    (home / ".claude" / "projects" / "-w").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HANDOFFS_DIR", str(home / "handoffs"))
    monkeypatch.setenv("ACCOUNTS_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_ROOTS", str(home / ".claude"))
    live(monkeypatch)
    return home


def live(monkeypatch, *sessions):
    def run(argv):
        return "\n".join(sessions) + "\n" if argv[0] == "tmux" else "abc1234\n"

    monkeypatch.setattr(remote_jobs, "run", run)


def write_job(box, slug="demo"):
    directory = box / "handoffs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "job.json").write_text(
        json.dumps(
            {
                "slug": slug,
                "branch": "andrew/demo",
                "worktree": "/w",
                "origin_session": "",
                "sent_at": 1000,
            }
        )
    )
    return directory


def write_router_state(box, session_id, label="team-1", handoffs=0, pid=None, **extra):
    """A live router by default: the file is named for this test's own pid."""
    state = box.parent / "state" / f"account-router-{pid or os.getpid()}.json"
    state.write_text(
        json.dumps(
            {"session_id": session_id, "cwd": "/w", "label": label, "handoffs": handoffs, **extra}
        )
    )


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


def dead_pid():
    """A pid that just exited, so nothing can be running under it."""
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


def only_job(payload):
    return payload["jobs"]["demo"]


class TestState:
    @pytest.mark.parametrize("command,cwd,tmux,expected", [
        ("codex", "/w", True, "running"),
        ("bash", "/w", True, "gone"),
        ("codex", "/elsewhere", True, "gone"),
        ("codex", "/w", False, "gone"),
        (None, "/w", True, "gone"),
    ])
    def test_codex_job_requires_its_live_process_and_tmux(
        self, box, monkeypatch, command, cwd, tmux, expected
    ):
        write_job(box)
        process = box / "proc" / "123"
        process.mkdir(parents=True)
        if command:
            (process / "comm").write_text(command + "\n")
        (process / "cwd").symlink_to(cwd)
        original_children = remote_jobs.children
        monkeypatch.setattr(remote_jobs, "children", lambda p:
            [process] if p == Path("/proc") else original_children(p))
        live(monkeypatch, *( ["demo"] if tmux else []))

        assert only_job(remote_jobs.build(2000.0))["state"] == expected

    def test_a_job_whose_session_and_router_are_alive_and_has_no_report_is_running(
        self, box, monkeypatch
    ):
        write_job(box)
        write_router_state(box, "s-1")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "running"

    def test_a_job_with_no_session_and_no_report_is_gone(self, box, monkeypatch):
        write_job(box)
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "gone"

    def test_a_session_left_at_a_shell_after_its_router_exited_is_gone(
        self, box, monkeypatch
    ):
        write_job(box)
        write_router_state(box, "s-1", pid=dead_pid())
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "gone"

    def test_a_job_naming_no_worktree_falls_back_to_the_tmux_session(self, box, monkeypatch):
        directory = write_job(box)
        job = json.loads((directory / "job.json").read_text())
        del job["worktree"]
        (directory / "job.json").write_text(json.dumps(job))
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        assert only_job(remote_jobs.build(2000.0))["state"] == "running"

    def test_a_router_holding_for_a_reset_publishes_held_and_when(self, box, monkeypatch):
        write_job(box)
        write_router_state(box, "s-1", held_until=5000, held_reason="session limit")
        live(monkeypatch, "demo")
        monkeypatch.setattr(remote_jobs, "report_status", lambda _path: None)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["state"], row["held_until"]) == ("held", 5000)

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
    def test_the_account_and_move_count_come_from_the_state_sharing_the_worktree(self, box):
        write_job(box)
        write_router_state(box, "s-1", label="team-2", handoffs=3)

        row = only_job(remote_jobs.build(2000.0))

        assert (row["account"], row["handoffs"]) == ("team-2", 3)

    def test_a_state_for_another_worktree_is_not_this_job(self, box):
        write_job(box)
        (box.parent / "state" / f"account-router-{os.getpid()}.json").write_text(
            json.dumps({"session_id": "s-1", "cwd": "/elsewhere", "label": "team-2"})
        )

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

    def test_a_job_lists_the_workflows_of_the_session_its_router_state_names(self, box):
        write_job(box)
        write_router_state(box, "s-1")
        write_workflow(box, "s-1", "review loop", started=("a",))

        workflows = only_job(remote_jobs.build(2000.0))["workflows"]

        assert [row["agents_started"] for row in workflows] == [1]


def test_a_directory_without_a_job_file_is_not_a_job(box):
    (box / "handoffs" / "scratch").mkdir()

    assert remote_jobs.build(2000.0)["jobs"] == {}
