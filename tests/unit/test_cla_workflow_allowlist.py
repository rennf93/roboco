"""``.github/workflows/cla.yml`` self-satisfies for system-account PRs.

Regression guard for the 2026-07-27 roadmap item whose materialization was
lost to the board-program persistence bug (then the NAS rollback): the fix
itself — allowlisting ``roboco-app[bot]`` so the fleet's own assembled PRs
don't demand a signature the App account can never post — is a single line
in a YAML file with no code path exercising it, so nothing else in the test
suite would notice a silent revert. Parse the workflow directly instead of
trusting prose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

# tests/unit/test_cla_workflow_allowlist.py
#   parents[0] = unit
#   parents[1] = tests
#   parents[2] = <repo root>
_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "cla.yml"


def _cla_step() -> dict[str, Any]:
    doc = cast("dict[str, Any]", yaml.safe_load(_WORKFLOW.read_text()))
    steps = doc["jobs"]["cla"]["steps"]
    (step,) = [
        s for s in steps if s.get("uses", "").startswith("contributor-assistant/")
    ]
    return cast("dict[str, Any]", step)


def _allowlist() -> set[str]:
    step = _cla_step()
    raw = cast("str", step["with"]["allowlist"])
    return {entry.strip() for entry in raw.split(",")}


def _pull_request_target_types() -> set[str]:
    doc = cast("dict[Any, Any]", yaml.safe_load(_WORKFLOW.read_text()))
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1 rules.
    triggers = cast("dict[str, Any]", doc[True])
    return set(triggers["pull_request_target"]["types"])


def _workflow_permissions() -> dict[str, str]:
    doc = cast("dict[str, Any]", yaml.safe_load(_WORKFLOW.read_text()))
    return cast("dict[str, str]", doc["permissions"])


def test_app_bot_is_allowlisted() -> None:
    # The App account authors/commits on essentially every fleet PR and can
    # never post the sign-off comment as itself — it must be exempt.
    assert "roboco-app[bot]" in _allowlist()


def test_dependabot_is_allowlisted() -> None:
    assert "dependabot[bot]" in _allowlist()


def test_cla_runs_unconditionally_on_pull_request_target() -> None:
    # Self-satisfy means the check evaluates on every PR event, not only when
    # a human posts the sign-off comment — otherwise an allowlisted author's
    # PR would sit unchecked until someone manually commented "recheck".
    step = _cla_step()
    assert "github.event_name == 'pull_request_target'" in step["if"]


def test_cla_step_still_allows_the_human_recheck_trigger() -> None:
    # The unconditional pull_request_target clause must stay additive, not a
    # replacement — a human contributor's "recheck"/sign-off comment on an
    # existing PR still has to retrigger the check.
    step = _cla_step()
    assert "github.event.comment.body == 'recheck'" in step["if"]


def test_arbitrary_untrusted_account_is_not_allowlisted() -> None:
    # Guards the opposite failure mode from the one this task fixes: the
    # allowlist must stay a specific, named set, not silently widen (e.g. a
    # future edit collapsing it to a wildcard) into exempting everyone.
    assert "some-untrusted-fork-account" not in _allowlist()


def test_pull_request_target_fires_on_open_and_sync() -> None:
    # The step's unconditional `if:` only self-satisfies when the workflow's
    # `on:` trigger actually runs at PR creation/update — dropping "opened"
    # or "synchronize" from `pull_request_target.types` would silently defer
    # the check to a later event (e.g. only "closed"), reintroducing a
    # window where an allowlisted author's fresh PR sits unchecked.
    types = _pull_request_target_types()
    assert {"opened", "synchronize"} <= types


def test_workflow_has_contents_write_permission() -> None:
    # Self-satisfying for an allowlisted author still requires the action to
    # successfully record a (skipped) signature entry on the cla-signatures
    # branch; a regression to read-only contents permission 403s that write
    # ("Resource not accessible by integration") even for exempt authors,
    # per the inline comment at the top of the permissions block.
    assert _workflow_permissions()["contents"] == "write"


def test_workflow_has_pull_requests_write_permission() -> None:
    # The action posts the pass/fail status comment onto the PR itself, so
    # an allowlisted author's PR only visibly self-satisfies (rather than
    # silently succeeding with no user-facing confirmation) if the workflow
    # can still write to the pull request.
    assert _workflow_permissions()["pull-requests"] == "write"


def test_workflow_has_statuses_write_permission() -> None:
    # The "CLA check passes" half of the acceptance criterion is the commit
    # status the action sets on the PR's head SHA (what a required-status
    # branch protection rule actually gates on) — a regression to read-only
    # `statuses` would leave an allowlisted author's PR without the green
    # check even though the sign-off step itself ran and skipped correctly.
    assert _workflow_permissions()["statuses"] == "write"


def test_workflow_has_actions_write_permission() -> None:
    # contributor-assistant/github-action re-triggers itself (e.g. after
    # recording a signature) via the Actions API — dropping this permission
    # would silently break that self-retrigger even though the other three
    # permissions still let it read the PR and write the status/signature.
    assert _workflow_permissions()["actions"] == "write"
