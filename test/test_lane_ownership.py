"""Unit tests for bin/lane_ownership.py — no network: every GitHub read is a stub."""

import json

import pytest

import lane_ownership

TICKET = "ABC-1"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def lanes(**owned):
    return {"version": 1, "lanes": owned}


def write_lanes(doc):
    lane_ownership.write_json(lane_ownership.lanes_path(), doc)


def no_repo():
    raise AssertionError("a PR that names its repository needs no lookup")


def github(data, calls=None):
    """A stub `gh api graphql`: records each call and answers with DATA, as gh prints it."""

    def run(argv):
        if calls is not None:
            calls.append(argv)
        return json.dumps({"data": data})

    return run


def pr_node(state="OPEN", rollup="SUCCESS", **fields):
    return {
        "state": state,
        "isDraft": False,
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeable": "MERGEABLE",
        "isInMergeQueue": False,
        "autoMergeRequest": None,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": rollup}}}]},
        **fields,
    }


class TestItems:
    @pytest.mark.parametrize(
        "text",
        ["acme/app#101", "https://github.com/acme/app/pull/101", "Acme/App#101"],
    )
    def test_every_spelling_of_a_pr_is_one_key(self, text):
        assert lane_ownership.parse_item(text, no_repo) == ("prs", "acme/app#101")

    def test_a_bare_number_takes_the_repository_given(self):
        assert lane_ownership.parse_item("#7", lambda: "Acme/Tools") == (
            "prs",
            "acme/tools#7",
        )

    def test_a_bare_number_needs_a_repository_with_its_owner(self):
        with pytest.raises(lane_ownership.LaneError):
            lane_ownership.parse_item("101", lambda: "app")

    def test_a_ticket_is_its_upper_case_id(self):
        assert lane_ownership.parse_item("ABC-1", no_repo) == ("tickets", "ABC-1")

    def test_a_lower_case_ticket_is_refused_rather_than_guessed(self):
        with pytest.raises(lane_ownership.LaneError):
            lane_ownership.parse_item("abc-1", no_repo)

    def test_a_photon_lane_is_named_with_its_prefix(self):
        assert lane_ownership.parse_item("photon:sol-spend-cap", no_repo) == ("photon", "sol-spend-cap")


class TestOwnership:
    def test_a_lane_that_takes_an_item_takes_it_from_the_lane_that_held_it(self):
        doc = lanes(infra={"prs": ["acme/app#102"], "tickets": [], "photon": []})

        lane_ownership.give(doc, "agent", [("prs", "acme/app#102")])

        assert doc["lanes"]["infra"]["prs"] == []
        assert doc["lanes"]["agent"]["prs"] == ["acme/app#102"]

    def test_a_dropped_item_leaves_every_lane(self):
        doc = lanes(infra={"tickets": ["ABC-1"]}, agent={"tickets": ["ABC-1"]})

        taken = lane_ownership.drop(doc, [("tickets", "ABC-1")])

        assert sorted(taken) == ["agent", "infra"]
        assert doc["lanes"]["infra"]["tickets"] == doc["lanes"]["agent"]["tickets"] == []

    def test_a_key_added_by_hand_survives_an_edit(self):
        doc = lanes(infra={"prs": [], "note": "kept by hand"})

        lane_ownership.give(doc, "infra", [("tickets", "ABC-1")])

        assert doc["lanes"]["infra"]["note"] == "kept by hand"

    def test_a_list_a_hand_edit_broke_is_never_overwritten(self):
        path = lane_ownership.lanes_path()
        path.parent.mkdir(parents=True)
        path.write_text('{"lanes": {"infra": ')

        with pytest.raises(lane_ownership.LaneError):
            with lane_ownership.editing() as doc:
                lane_ownership.give(doc, "agent", [("tickets", "ABC-1")])

        assert path.read_text() == '{"lanes": {"infra": '

    @pytest.mark.parametrize(
        "entry",
        [
            {"infra": {"prs": "acme/app#101"}},
            {"infra": {"prs": [101]}},
            {"infra": {"prs": ["Acme/App#101"]}},
            {"infra": "see notes"},
            {"Infra": {"prs": []}},
        ],
    )
    def test_a_hand_edit_in_a_form_lane_own_does_not_write_is_refused_not_rewritten(self, entry):
        write_lanes(lanes(**entry))
        before = lane_ownership.lanes_path().read_text()

        with pytest.raises(lane_ownership.LaneError):
            with lane_ownership.editing() as doc:
                lane_ownership.give(doc, "agent", [("tickets", "ABC-2")])

        assert lane_ownership.lanes_path().read_text() == before


class TestRead:
    def test_every_listed_pr_in_every_repository_is_read_in_one_call(self):
        calls = []
        data = {"r0": {"p101": pr_node(), "p102": pr_node()}, "r1": {"p7": pr_node()}}

        found = lane_ownership.read_prs(
            {"acme/app#102", "acme/app#101", "acme/tools#7"},
            github(data, calls),
        )

        assert len(calls) == 1
        assert set(found) == {"acme/app#102", "acme/app#101", "acme/tools#7"}

    @pytest.mark.parametrize(
        "fields, status",
        [
            ({"isInMergeQueue": True, "autoMergeRequest": {"enabledAt": "t"}}, "queued"),
            ({"autoMergeRequest": {"enabledAt": "t"}, "isDraft": True}, "armed"),
            ({"isDraft": True, "mergeable": "CONFLICTING"}, "draft"),
            ({"mergeable": "CONFLICTING", "rollup": "FAILURE"}, "conflicts"),
            ({"rollup": "ERROR", "reviewDecision": "CHANGES_REQUESTED"}, "failing"),
            ({"reviewDecision": "CHANGES_REQUESTED", "rollup": "PENDING"}, "changes"),
            ({"rollup": "PENDING", "reviewDecision": "APPROVED"}, "running"),
            ({"reviewDecision": "APPROVED"}, "approved"),
            ({}, "open"),
        ],
    )
    def test_a_pr_reads_as_the_most_pressing_thing_holding_it(self, fields, status):
        assert lane_ownership.pr_status(pr_node(**fields)) == status


class TestRefresh:
    def test_a_merged_or_closed_pr_leaves_the_list_and_an_open_one_keeps_its_state(self):
        write_lanes(lanes(infra={"prs": ["o/r#1", "o/r#2", "o/r#3"], "tickets": ["ABC-1"]}))
        data = {"r0": {"p1": pr_node(state="MERGED"), "p2": pr_node(state="CLOSED"), "p3": pr_node()}}

        states = lane_ownership.refresh(now=5000.0, runner=github(data))

        assert lane_ownership.load_lanes()["lanes"]["infra"]["prs"] == ["o/r#3"]
        assert states["prs"] == {"o/r#3": {"state": "OPEN", "status": "open"}}

    def test_a_pr_github_has_no_record_of_stays_listed_as_not_found(self):
        write_lanes(lanes(infra={"prs": ["o/r#9"]}))

        states = lane_ownership.refresh(now=5000.0, runner=github({"r0": {"p9": None}}))

        assert lane_ownership.load_lanes()["lanes"]["infra"]["prs"] == ["o/r#9"]
        assert states["prs"]["o/r#9"]["status"] == "missing"

    def test_a_failed_read_keeps_the_last_states_and_says_it_failed(self):
        write_lanes(lanes(infra={"prs": ["o/r#3"]}))
        lane_ownership.refresh(now=5000.0, runner=github({"r0": {"p3": pr_node()}}))

        states = lane_ownership.refresh(now=5600.0, runner=lambda argv: "")

        assert states["checked_at"] == 5000
        assert states["attempted_at"] == 5600
        assert states["prs"] == {"o/r#3": {"state": "OPEN", "status": "open"}}
        assert states["error"]

    def test_the_poll_reads_again_only_past_the_interval_or_for_a_pr_never_read(self):
        write_lanes(lanes(infra={"prs": ["o/r#3"]}))
        calls = []
        runner = github({"r0": {"p3": pr_node(), "p4": pr_node()}}, calls)

        lane_ownership.refresh_if_due(5000.0, runner)
        lane_ownership.refresh_if_due(5089.0, runner)
        write_lanes(lanes(infra={"prs": ["o/r#3", "o/r#4"]}))
        lane_ownership.refresh_if_due(5089.0, runner)
        lane_ownership.refresh_if_due(5179.0, runner)

        assert len(calls) == 3

    def test_a_list_the_poll_cannot_read_never_stops_the_poll(self):
        lane_ownership.lanes_path().parent.mkdir(parents=True)
        lane_ownership.lanes_path().write_text('{"lanes": {"Infra": {}}}')

        assert lane_ownership.refresh_if_due(5000.0, github({})) is None

    def test_with_no_pr_listed_the_poll_does_nothing(self):
        write_lanes(lanes(infra={"tickets": ["ABC-1"]}))
        calls = []

        lane_ownership.refresh_if_due(5000.0, github({}, calls))

        assert calls == []
        assert not lane_ownership.states_path().exists()

    def test_a_read_asked_for_with_no_pr_listed_makes_no_github_call(self):
        write_lanes(lanes(infra={"tickets": ["ABC-1"]}))
        calls = []

        lane_ownership.refresh(5000.0, github({}, calls))

        assert calls == []


class TestCommand:
    def test_a_pr_given_on_the_command_line_is_listed_and_read_at_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(lane_ownership, "run_gh", github({"r0": {"p103": pr_node()}}, calls))

        assert lane_ownership.main(["give", "Agent", "--repo", "acme/app", "103", "ABC-2"]) == 0

        assert lane_ownership.load_lanes()["lanes"]["agent"]["prs"] == ["acme/app#103"]
        assert lane_ownership.read_states()["prs"]["acme/app#103"]["status"] == "open"

    def test_show_lists_the_last_read_error_then_each_lane_with_what_it_owns(self):
        doc = lanes(infra={"prs": ["o/r#1", "o/r#2"], "tickets": [TICKET], "photon": ["gate-watch"]})
        states = {"error": "GitHub read failed", "prs": {"o/r#1": {"state": "OPEN", "status": "failing"}}}

        assert lane_ownership.show(doc, states, None) == (
            "last PR read failed: GitHub read failed\n"
            f"infra\n  o/r#1  failing\n  o/r#2  not read yet\n  {TICKET}\n  photon:gate-watch"
        )
