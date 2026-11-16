#!/usr/bin/env python3
"""Which laptop lane owns which PRs, tickets and photon lanes, and the state of each owned PR.

A lane is named the way `claude --name` or `/rename` names a session, so the list outlives every
relaunch. Lanes keep it current with this command, or by editing `~/.accounts/lanes.json`. The PR
states are read on the account poll, in one GitHub query for every listed PR, and never on a render.

    lane-own give <lane> <item>...   the lane takes each item from whichever lane held it
    lane-own drop <item>...          no lane holds the items any more
    lane-own show [lane]             each lane with what it owns and each PR's state
    lane-own refresh                 read every listed PR's state now

An item is a PR (`owner/repo#<number>`, its URL, or a bare `<number>` in the current repository or
the one `--repo owner/repo` names), a ticket (`<KEY>-<number>`, upper case), or a photon lane
(`photon:<slug>`).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
# The status line's PR cache keeps a read this long; the poll reads no more often.
REFRESH_INTERVAL_S = 90
GH_TIMEOUT_S = 30
KINDS = ("prs", "tickets", "photon")
LANE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}\Z")
PR_KEY = re.compile(r"([a-z0-9_.-]+)/([a-z0-9_.-]+)#([0-9]+)\Z")
PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([0-9]+)/?\Z")
PR_REF = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([0-9]+)\Z")
PR_NUMBER = re.compile(r"#?([0-9]+)\Z")
TICKET = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+\Z")
PHOTON_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
GONE_STATES = {"MERGED", "CLOSED"}
ITEM_FORMS = {"prs": PR_KEY, "tickets": TICKET, "photon": PHOTON_SLUG}
ITEM_WORDS = {
    "prs": "lower case owner/repo#<number> keys",
    "tickets": "upper case <KEY>-<number> ids",
    "photon": "lower case photon lane slugs",
}
PR_FIELDS = (
    "state isDraft reviewDecision mergeable isInMergeQueue autoMergeRequest { enabledAt }"
    " commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"
)


class LaneError(Exception):
    pass


def lanes_path() -> Path:
    return Path.home() / ".accounts" / "lanes.json"


def states_path() -> Path:
    return Path.home() / ".accounts" / "lane-prs.json"


def write_json(path: Path, payload) -> None:
    """Replaced whole, so a reader sees one version or the next."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def empty_doc() -> dict:
    return {"version": VERSION, "lanes": {}}


def load_lanes() -> dict:
    """The list as written; a file a hand edit broke is an error, never read as empty and overwritten."""
    path = lanes_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return empty_doc()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LaneError(f"{path} is not valid JSON ({exc}); fix it by hand") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("lanes"), dict):
        raise LaneError(f"{path} has no lanes map; fix it by hand")
    for lane, entry in doc["lanes"].items():
        check_lane(path, lane, entry)
    return doc


def check_lane(path: Path, lane, entry) -> None:
    """Every entry in the form `lane-own` writes, so an edit never rewrites what a hand put there."""
    if not isinstance(lane, str) or not LANE_NAME.match(lane):
        raise LaneError(f"{path}: {lane!r} is not a lane name: lower case letters, digits, '.', '_' or '-'")
    if not isinstance(entry, dict):
        raise LaneError(f"{path}: lane {lane} is not an object")
    for kind, pattern in ITEM_FORMS.items():
        items = entry.get(kind, [])
        if not isinstance(items, list) or not all(isinstance(i, str) and pattern.match(i) for i in items):
            raise LaneError(f"{path}: {lane}.{kind} must be a list of {ITEM_WORDS[kind]}")


@contextmanager
def editing():
    """The list, loaded under a lock and written back when the block ends without an error."""
    path = lanes_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load_lanes()
        yield doc
        write_json(path, doc)


def lane_entry(doc: dict, lane: str) -> dict:
    entry = doc["lanes"].get(lane)
    return entry if isinstance(entry, dict) else {}


def lane_items(doc: dict, lane: str) -> dict[str, list[str]]:
    entry = lane_entry(doc, lane)
    return {kind: list(entry.get(kind) or []) for kind in KINDS}


def lane_name(text: str) -> str:
    name = text.strip().lower()
    if not LANE_NAME.match(name):
        raise LaneError(f"{text!r} is not a lane name: letters, digits, '.', '_' or '-', at most 32")
    return name


def parse_item(text: str, default_repo) -> tuple[str, str]:
    """(kind, canonical value). default_repo is called only for a bare PR number."""
    text = text.strip()
    if text.startswith("photon:"):
        slug = text[len("photon:"):]
        if not PHOTON_SLUG.match(slug):
            raise LaneError(f"{text!r}: a photon lane's slug is lowercase kebab-case")
        return "photon", slug
    if TICKET.match(text):
        return "tickets", text
    match = PR_URL.match(text) or PR_REF.match(text)
    if match:
        owner, name, number = match.groups()
        return "prs", f"{owner}/{name}#{int(number)}".lower()
    match = PR_NUMBER.match(text)
    if match:
        key = f"{default_repo()}#{int(match.group(1))}".lower()
        if not PR_KEY.match(key):
            raise LaneError(f"{text!r}: --repo must name owner/repo, not {key.split('#')[0]!r}")
        return "prs", key
    raise LaneError(
        f"{text!r} is not an item: a PR is owner/repo#<number>, its URL or <number>; a ticket is "
        "<KEY>-<number> in upper case; a photon lane is photon:<slug>"
    )


def give(doc: dict, lane: str, items: list[tuple[str, str]]) -> None:
    """The lane holds each item, and no other lane does."""
    drop(doc, items)
    entry = lane_items(doc, lane)
    for kind, value in items:
        if value not in entry[kind]:
            entry[kind].append(value)
    doc["lanes"][lane] = {**lane_entry(doc, lane), **entry}


def drop(doc: dict, items: list[tuple[str, str]]) -> list[str]:
    """Removes each item from every lane; the lanes it was taken from."""
    taken = []
    for lane in list(doc["lanes"]):
        entry = lane_items(doc, lane)
        for kind, value in items:
            if value in entry[kind]:
                entry[kind].remove(value)
                taken.append(lane)
        doc["lanes"][lane] = {**lane_entry(doc, lane), **entry}
    return taken


def listed_prs(doc: dict) -> set[str]:
    return {pr for lane in doc["lanes"] for pr in lane_items(doc, lane)["prs"] if PR_KEY.match(pr)}


def run_gh(argv: list[str]) -> str:
    """gh's stdout: a GraphQL read that hits a missing PR exits 1 and still prints the rest."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def read_prs(keys: set[str], runner) -> dict[str, dict | None] | None:
    """Every key's PR as GitHub has it now, None where GitHub has no such PR, in one query.
    None when the read failed."""
    repos: dict[tuple[str, str], list[int]] = {}
    for key in keys:
        owner, name, number = PR_KEY.match(key).groups()
        repos.setdefault((owner, name), []).append(int(number))
    params, fields, variables, aliases = [], [], [], {}
    for i, ((owner, name), numbers) in enumerate(sorted(repos.items())):
        params += [f"$o{i}: String!", f"$n{i}: String!"]
        variables += ["-f", f"o{i}={owner}", "-f", f"n{i}={name}"]
        prs = " ".join(f"p{n}: pullRequest(number: {n}) {{ {PR_FIELDS} }}" for n in sorted(numbers))
        fields.append(f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ {prs} }}")
        aliases.update({f"{owner}/{name}#{n}": (f"r{i}", f"p{n}") for n in numbers})
    query = f"query({', '.join(params)}) {{ {' '.join(fields)} }}"
    try:
        data = json.loads(runner(["gh", "api", "graphql", "-f", f"query={query}", *variables]))["data"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    found = {}
    for key, (repository, pr) in aliases.items():
        node = (data.get(repository) or {}).get(pr)
        found[key] = node if isinstance(node, dict) else None
    return found


def pr_status(node: dict) -> str:
    """The one word a lane acts on, most pressing first: what holds it, then what it waits on."""
    commits = (node.get("commits") or {}).get("nodes") or [{}]
    rollup = ((commits[-1].get("commit") or {}).get("statusCheckRollup") or {}).get("state")
    review = node.get("reviewDecision")
    if node.get("isInMergeQueue"):
        return "queued"
    if node.get("autoMergeRequest"):
        return "armed"
    if node.get("isDraft"):
        return "draft"
    if node.get("mergeable") == "CONFLICTING":
        return "conflicts"
    if rollup in ("FAILURE", "ERROR"):
        return "failing"
    if review == "CHANGES_REQUESTED":
        return "changes"
    if rollup in ("PENDING", "EXPECTED"):
        return "running"
    if review == "APPROVED":
        return "approved"
    return "open"


def refresh(now: float, runner=None) -> dict:
    """Reads every listed PR and writes their states; a PR read as merged or closed leaves the list.
    A failed read keeps the last states and records the attempt."""
    previous = read_states()
    keys = listed_prs(load_lanes())
    found = read_prs(keys, runner or run_gh) if keys else {}
    if found is None:
        states = {**previous, "attempted_at": int(now), "error": "GitHub read failed"}
        write_json(states_path(), states)
        return states
    prs = {}
    for key, node in found.items():
        if node is None:
            prs[key] = {"state": "MISSING", "status": "missing"}
        elif node.get("state") == "OPEN":
            prs[key] = {"state": "OPEN", "status": pr_status(node)}
        elif node.get("state") in GONE_STATES:
            prs[key] = {"state": node["state"]}
    gone = [("prs", key) for key, pr in prs.items() if pr["state"] in GONE_STATES]
    if gone:
        with editing() as doc:
            drop(doc, gone)
    states = {
        "version": VERSION,
        "checked_at": int(now),
        "attempted_at": int(now),
        "error": None,
        "prs": {key: pr for key, pr in prs.items() if pr["state"] not in GONE_STATES},
    }
    write_json(states_path(), states)
    return states


def read_states() -> dict:
    try:
        states = json.loads(states_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "checked_at": 0, "prs": {}}
    return states if isinstance(states, dict) else {"version": VERSION, "checked_at": 0, "prs": {}}


def refresh_if_due(now: float, runner=None) -> None:
    """The account poll's step: a read when the last attempt is older than the interval, or a listed
    PR has no state yet. Never raises, so a bad list cannot stop the poll."""
    try:
        listed = listed_prs(load_lanes())
        if not listed:
            return
        states = read_states()
        unread = listed - set(states.get("prs") or {})
        last = states.get("attempted_at") or states.get("checked_at") or 0
        if unread or now - float(last) >= REFRESH_INTERVAL_S:
            refresh(now, runner)
    except (LaneError, OSError, ValueError, TypeError):
        return


def current_repo() -> str:
    repo = run_gh(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()
    if not repo:
        raise LaneError("a bare PR number needs --repo owner/repo, or a GitHub repository here")
    return repo


def show(doc: dict, states: dict, only: str | None) -> str:
    lines = [f"last PR read failed: {states['error']}"] if states.get("error") else []
    prs = states.get("prs") or {}
    for lane in sorted(doc["lanes"]):
        if only and lane != only:
            continue
        items = lane_items(doc, lane)
        lines.append(lane)
        for pr in items["prs"]:
            lines.append(f"  {pr}  {(prs.get(pr) or {}).get('status', 'not read yet')}")
        lines += [f"  {ticket}" for ticket in items["tickets"]]
        lines += [f"  photon:{slug}" for slug in items["photon"]]
    if only and only not in doc["lanes"]:
        lines.append(f"no lane named {only}")
    return "\n".join(lines)


def repo_option(argv: list[str]) -> tuple[list[str], str | None]:
    """--repo may come anywhere among the items: `give infra --repo meetsol/iris 8987 8986`."""
    rest, repo = [], None
    args = iter(argv)
    for arg in args:
        if arg == "--repo":
            repo = next(args, None)
        else:
            rest.append(arg)
    return rest, repo


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="lane-own", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    give_parser = sub.add_parser("give", help="the lane takes each item from whichever lane held it")
    give_parser.add_argument("lane")
    give_parser.add_argument("items", nargs="*")
    drop_parser = sub.add_parser("drop", help="no lane holds the items any more")
    drop_parser.add_argument("items", nargs="+")
    show_parser = sub.add_parser("show", help="each lane with what it owns")
    show_parser.add_argument("lane", nargs="?")
    sub.add_parser("refresh", help="read every listed PR's state now")
    argv, repo_flag = repo_option(argv)
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            print(show(load_lanes(), read_states(), lane_name(args.lane) if args.lane else None))
            return 0
        if args.command == "refresh":
            states = refresh(time.time())
            open_prs = [pr for pr in (states.get("prs") or {}).values() if pr.get("state") == "OPEN"]
            print(states.get("error") or f"read {len(open_prs)} open PRs")
            return 1 if states.get("error") else 0
        repo = (lambda: repo_flag) if repo_flag else current_repo
        items = [parse_item(text, repo) for text in args.items]
        if args.command == "give":
            lane = lane_name(args.lane)
            with editing() as doc:
                give(doc, lane, items)
            named = [f"photon:{value}" if kind == "photon" else value for kind, value in items]
            print(f"{lane} owns {', '.join(named) or 'its list as before'}")
            refresh_if_due(time.time())
            return 0
        with editing() as doc:
            taken = drop(doc, items)
        print(f"dropped from {', '.join(sorted(set(taken)))}" if taken else "no lane held them")
        return 0
    except LaneError as exc:
        print(f"lane-own: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
