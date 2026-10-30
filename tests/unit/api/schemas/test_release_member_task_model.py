"""``ReleaseProposalResponse.member_task_ids`` wire-shape contract lock.

pr_gate finding F-b7ba8602: the field ships as a list of ``{task_id,
pr_number}`` OBJECTS (not bare id strings), which is the confirmed contract
with the consuming frontend: the panel's ``ReleaseMemberTaskId`` in
``panel/src/lib/api/release.ts`` (PR #1069, branch
feature/frontend/0e923eb8--54e30535--07f3373f) types
``member_task_ids: ReleaseMemberTaskId[]`` and maps ``m.task_id`` per member
into the release verification rollup. These tests pin the serialized shape
so a future refactor cannot silently break the FE seam.
"""

from __future__ import annotations

from roboco.api.schemas.release import (
    ReleaseMemberTaskModel,
    ReleaseProposalResponse,
    ReleaseReportModel,
)


def _report() -> ReleaseReportModel:
    return ReleaseReportModel(
        proposed_version="0.19.0",
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog="## [0.19.0]",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


def test_member_task_ids_serialize_as_objects() -> None:
    """Each member serializes as a {task_id, pr_number} object: the shape
    the FE's ReleaseMemberTaskId union expects (pr_gate F-b7ba8602)."""
    response = ReleaseProposalResponse(
        task_id="d6d8853c-0000-0000-0000-000000000000",
        title="Release proposal: v0.19.0",
        status="pending",
        report=_report(),
        member_task_ids=[
            ReleaseMemberTaskModel(
                task_id="140831f7-0000-0000-0000-000000000000", pr_number=42
            )
        ],
    )
    data = response.model_dump()
    assert data["member_task_ids"] == [
        {
            "task_id": "140831f7-0000-0000-0000-000000000000",
            "pr_number": 42,
        }
    ]


def test_member_task_ids_pr_number_defaults_to_null() -> None:
    """A member without a PR serializes pr_number as null (the FE type is
    ``pr_number: number | null``), never a missing key."""
    response = ReleaseProposalResponse(
        task_id="d6d8853c-0000-0000-0000-000000000000",
        title="Release proposal: v0.19.0",
        status="pending",
        report=_report(),
        member_task_ids=[
            ReleaseMemberTaskModel(task_id="140831f7-0000-0000-0000-000000000000")
        ],
    )
    (member,) = response.model_dump()["member_task_ids"]
    assert member["pr_number"] is None
    assert set(member.keys()) == {"task_id", "pr_number"}


def test_member_task_ids_default_empty_and_always_present() -> None:
    """The field is always present on the wire; an empty list is a real
    state ("this release genuinely carries no member tasks") that the FE
    renders as an empty-state message, never as an absent key."""
    response = ReleaseProposalResponse(
        task_id="d6d8853c-0000-0000-0000-000000000000",
        title="Release proposal: v0.19.0",
        status="pending",
        report=_report(),
    )
    assert response.model_dump()["member_task_ids"] == []
