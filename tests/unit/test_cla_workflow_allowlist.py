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
