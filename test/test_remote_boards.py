"""Unit tests for bin/remote_boards.py — no network, no ssh."""

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import remote_boards  # noqa: E402

SNAPSHOT = {
    "version": 1,
    "generated_at": 1000,
    "accounts": {"team-1": {"five_hour": {"used_pct": 12}, "seven_day": {"used_pct": 30}}},
}
CODEX_USAGE = {"team-1": {"fetched_at": 1000, "rate_limits": {"primary": {"used_percent": 4}}}}
JOBS = {"version": 1, "generated_at": 1000, "jobs": {"demo": {"state": "running"}}}


def encoded(*documents):
    lines = [base64.b64encode(json.dumps(doc).encode()).decode() if doc else "" for doc in documents]
    return "\n".join(lines) + "\n"


def ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_boards, "REMOTE_ROOT", tmp_path / "remote")
    return tmp_path / "remote"


def conf(values):
    return lambda name: values.get(name, "")


class TestConfig:
    def test_entry_without_a_host_is_dropped(self):
        boards = remote_boards.parse_boards("devbox", conf({}))

        assert boards == []

    def test_board_takes_its_up_check_from_its_own_variable(self):
        boards = remote_boards.parse_boards(
            "devbox:devbox-host", conf({"REMOTE_BOARD_UP_DEVBOX": "is-it-up"})
        )

        assert boards == [remote_boards.Board("devbox", "devbox-host", "is-it-up")]


def test_the_fetch_asks_for_exactly_the_three_published_files():
    command = remote_boards.fetch_command()

    assert set(re.findall(r"\$HOME/[^\"\s]+", command)) == {
        "$HOME/.accounts/statusline-snapshot.json",
        "$HOME/.codex-accounts/usage.json",
        "$HOME/handoffs/jobs.json",
    }


def test_a_token_shaped_field_never_reaches_disk():
    scrubbed = remote_boards.scrub({"used_pct": 12, "accessToken": "not-a-credential", "nested": {"refresh_token": "x"}})

    assert scrubbed == {"used_pct": 12, "nested": {}}


def test_a_file_the_board_does_not_have_leaves_its_local_copy_alone():
    documents = remote_boards.decode_payload(encoded(SNAPSHOT, None))

    assert documents[1] is None


class TestRefreshBoard:
    def test_a_successful_pull_writes_only_the_board_files(self, root):
        board = remote_boards.Board("devbox", "devbox-host")

        remote_boards.refresh_board(
            board, now=2000.0, runner=lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, None))
        )

        assert sorted(entry.name for entry in (root / "devbox").iterdir()) == [
            "codex-usage.json",
            "meta.json",
            "statusline-snapshot.json",
        ]

    def test_a_failed_pull_keeps_the_last_numbers_and_records_the_error(self, root):
        board = remote_boards.Board("devbox", "devbox-host")
        remote_boards.refresh_board(
            board, now=2000.0, runner=lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, None))
        )

        remote_boards.refresh_board(
            board, now=2600.0, runner=lambda argv, timeout: subprocess.CompletedProcess([], 255, "", "")
        )

        meta = json.loads((root / "devbox" / "meta.json").read_text())
        assert meta["error"] == "ssh exit 255"
        assert meta["fetched_at"] == 2000.0
        assert json.loads((root / "devbox" / "statusline-snapshot.json").read_text()) == SNAPSHOT

    def test_a_failed_pull_records_what_ssh_said(self, root):
        board = remote_boards.Board("devbox", "devbox-host")
        stderr = "Token has expired and refresh failed\nConnection closed by UNKNOWN port 65535\n"

        remote_boards.refresh_board(
            board, now=2000.0, runner=lambda argv, timeout: subprocess.CompletedProcess([], 255, "", stderr)
        )

        meta = json.loads((root / "devbox" / "meta.json").read_text())
        assert meta["error"] == "ssh exit 255 \u00b7 Token has expired and refresh failed"

    def test_a_long_reason_is_bounded_to_one_row(self, root):
        board = remote_boards.Board("devbox", "devbox-host")
        stderr = "x" * 200

        remote_boards.refresh_board(
            board, now=2000.0, runner=lambda argv, timeout: subprocess.CompletedProcess([], 255, "", stderr)
        )

        reason = json.loads((root / "devbox" / "meta.json").read_text())["error"]
        assert reason.endswith("\u2026")
        assert len(reason) == len("ssh exit 255 \u00b7 ") + remote_boards.REASON_MAX_CHARS

    def test_a_board_its_up_check_calls_down_is_never_contacted(self, root):
        board = remote_boards.Board("devbox", "devbox-host", "is-it-up")
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "")

        remote_boards.refresh_board(board, now=2000.0, runner=runner)

        assert [argv[0] for argv in calls] == ["bash"]

    @pytest.mark.parametrize("status, up", [(0, True), (1, False), (2, None), (3, None), (None, None)])
    def test_only_exit_1_calls_a_board_down(self, status, up):
        assert remote_boards.board_up(status) is up

    def test_an_up_check_that_exits_2_records_an_expired_login(self, root):
        board = remote_boards.Board("devbox", "devbox-host", "is-it-up")

        def runner(argv, timeout):
            if argv[0] == "bash":
                return subprocess.CompletedProcess(argv, 2, "", "")
            return ok(encoded(SNAPSHOT, CODEX_USAGE, None))

        meta = remote_boards.refresh_board(board, now=2000.0, runner=runner)

        assert meta["probe"] == "auth"


class TestRefreshAll:
    def test_a_board_polled_within_the_interval_is_left_alone(self, root):
        boards = "devbox:devbox-host"
        settings = conf({"REMOTE_ACCOUNT_BOARDS": boards, "REMOTE_BOARD_PULL_INTERVAL": "120"})
        remote_boards.refresh_all(
            settings, now=2000.0, runner=lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, None))
        )

        results = remote_boards.refresh_all(
            settings, now=2060.0, runner=lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, None))
        )

        assert results == []

    def test_a_board_dropped_from_the_config_loses_its_pulled_copy(self, root):
        settings = conf({"REMOTE_ACCOUNT_BOARDS": "devbox:devbox-host"})
        remote_boards.refresh_all(
            settings, now=2000.0, runner=lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, None))
        )

        remote_boards.refresh_all(conf({}), now=2000.0, runner=lambda argv, timeout: ok())

        assert not (root / "devbox").exists()


class TestRunningJobs:
    def test_a_board_running_a_job_is_pulled_again_inside_the_board_interval(self, root):
        settings = conf({"REMOTE_ACCOUNT_BOARDS": "devbox:devbox-host", "REMOTE_BOARD_PULL_INTERVAL": "120"})
        pull = lambda argv, timeout: ok(encoded(SNAPSHOT, CODEX_USAGE, JOBS))
        remote_boards.refresh_all(settings, now=2000.0, runner=pull)

        results = remote_boards.refresh_all(settings, now=2060.0, runner=pull)

        assert [meta["name"] for meta in results] == ["devbox"]
