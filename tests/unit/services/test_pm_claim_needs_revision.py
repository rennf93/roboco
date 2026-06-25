"""Runtime claim-status parity: PMs may re-claim a NEEDS_REVISION coordination task.

The lifecycle spec (``lifecycle.CLAIM_RULES``) lets ``CELL_PM`` / ``MAIN_PM``
claim ``NEEDS_REVISION`` so a rejected coordination / assembled task (pr_fail,
qa_fail, ceo_reject) can be re-claimed via ``i_will_plan`` and re-delegated.

The runtime claim path (``TaskService.claim`` ->
``_get_valid_claim_statuses`` -> ``_ROLE_CLAIM_STATUSES``) must honour the same
authority. Otherwise the spec gate *allows* ``i_will_plan`` on a
``needs_revision`` root, but the composed ``claim()`` inside the verb returns
``None`` (source status not in the runtime mapping) -> the verb runner raises
``INVALID_STATE`` -> the PM can neither plan nor idle its own rejected root and
respawn-loops on it (observed live 2026-06-25 on the ``0e49e04e`` cell root,
~143 INVALID_STATE rejections across 11 PM sessions).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from roboco.foundation.policy import lifecycle as spec
from roboco.models.base import TaskStatus
from roboco.services.task import _default_claim_statuses, _get_valid_claim_statuses

if TYPE_CHECKING:
    from roboco.db.tables import AgentTable


@pytest.mark.parametrize("role", ["cell_pm", "main_pm"])
def test_pm_runtime_claim_statuses_include_needs_revision(role: str) -> None:
    # _get_valid_claim_statuses only reads ``agent.role`` — a lightweight
    # role-bearing stand-in is enough; cast keeps it type-clean (no AgentTable row).
    agent = cast("AgentTable", SimpleNamespace(role=role))
    assert TaskStatus.NEEDS_REVISION in _get_valid_claim_statuses(
        agent, allow_reassign=False
    )
    assert TaskStatus.NEEDS_REVISION in _default_claim_statuses(role)


@pytest.mark.parametrize("role", [spec.Role.CELL_PM, spec.Role.MAIN_PM])
def test_runtime_pm_claim_mapping_covers_spec_claim_rules(role: spec.Role) -> None:
    """The runtime mapping must cover every status the spec grants the role.

    Guards against the spec (CLAIM_RULES) and the runtime mapping
    (_ROLE_CLAIM_STATUSES) drifting apart again — the parity invariant.
    """
    runtime_values = {s.value for s in _default_claim_statuses(role.value)}
    for status in spec.CLAIM_RULES[role]:
        assert status.value in runtime_values, (
            f"runtime claim mapping for {role.value} is missing spec-allowed "
            f"status '{status.value}'"
        )
