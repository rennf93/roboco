"""Doc-stage bail rejections must name the real exit: i_documented.

Live loop (2026-07-02, b8fe0494): fe-doc respawned 26 times on a revision
pass — the docs were already written, the agent wouldn't call i_documented
for work it didn't author, and its bail attempts (i_am_blocked/unclaim) were
rejected with a generic remediate that never mentioned the one verb that IS
the exit from awaiting_documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import Context, can_invoke_intent


@dataclass
class _Task:
    status: object = "awaiting_documentation"
    assigned_to: object = None
    task_type: object = "code"
    team: object = "frontend"
    created_by: object = field(default_factory=uuid4)


def test_doc_block_rejection_points_at_i_documented() -> None:
    doc = uuid4()
    task = _Task(assigned_to=doc)
    decision = can_invoke_intent(
        Role.DOCUMENTER, "i_am_blocked", task, Context(actor_id=doc)
    )
    assert not decision.allowed
    assert "i_documented" in (decision.remediate or "")
