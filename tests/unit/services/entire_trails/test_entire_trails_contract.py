"""The trails bridge is written against recorded Entire CLI output.

``tests/fixtures/entire_cli/<version>/`` holds what the pinned CLI printed
for every command the bridge runs (recorded on rennf93/roboco spike trails,
2026-10-29): its ``--help`` (the flags it takes) and its ``--json`` output
(the fields the drain reads). The integration suite replays them through
the real drain; this module ties them to the pin, so bumping
``ENTIRE_VERSION`` without re-recording fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from roboco.services.entire_trails.cli import EntireCliResult

_REPO = Path(__file__).resolve().parents[4]
_FIXTURES = _REPO / "tests" / "fixtures" / "entire_cli"


def _pinned_version() -> str:
    text = (_REPO / "docker" / "orchestrator.Dockerfile").read_text()
    match = re.search(r"^ARG ENTIRE_VERSION=(\S+)$", text, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _recorded(name: str) -> str:
    return (_FIXTURES / _pinned_version() / name).read_text()


def _parsed(name: str) -> dict[str, Any]:
    return EntireCliResult(0, _recorded(name), "").json()


def test_the_recordings_are_of_the_pinned_cli() -> None:
    version = _pinned_version()
    assert (_FIXTURES / version).is_dir(), (
        f"no recordings for Entire {version}: re-record tests/fixtures/entire_cli "
        "with the new CLI before bumping the pin"
    )
    assert _recorded("VERSION").strip() == f"Entire CLI {version}"


def test_trail_create_prints_what_the_mapping_stores() -> None:
    created = _parsed("trail-create.json")
    assert isinstance(created["id"], str)
    assert isinstance(created["number"], int)
    assert str(created["url"]).startswith("https://entire.com/")


def test_trail_show_lists_each_change_by_repository_and_branch() -> None:
    changes = _parsed("trail-show.json")["changes"]
    assert isinstance(changes, list)
    assert changes
    for change in changes:
        assert {"branch", "repository", "number"} <= set(change)
        assert "/" not in change["repository"]


def test_finding_add_and_list_carry_the_ids_the_drain_reads() -> None:
    added = _parsed("finding-add.json")
    assert isinstance(added["id"], str)
    assert added["clientId"]
    listed = _parsed("finding-list.json")
    assert isinstance(listed["has_more"], bool)
    for finding in listed["findings"]:
        assert {"id", "clientId", "status"} <= set(finding)


def test_status_verbs_print_text_not_json() -> None:
    """resolve / dismiss / approve take no --json; the drain never parses them."""
    assert _recorded("finding-resolve.txt").startswith("Updated finding ")
    assert _recorded("finding-dismiss.txt").startswith("Updated finding ")
    assert _recorded("trail-approve.txt").startswith("Approved trail #")


def test_the_refusals_the_drain_must_survive_are_recorded() -> None:
    assert "client_id_conflict" in _recorded("error-finding-client-id-conflict.txt")
    assert "Idempotency-Key already used with a different request body" in _recorded(
        "error-create-idempotency-conflict.txt"
    )
    assert "Change already belongs to another Trail" in _recorded(
        "error-link-other-trail.txt"
    )
    assert "Cannot add a Change to a closed Trail" in _recorded(
        "error-link-closed-trail.txt"
    )


def test_every_recording_is_well_formed() -> None:
    for path in (_FIXTURES / _pinned_version()).glob("*.json"):
        json.loads(path.read_text())
