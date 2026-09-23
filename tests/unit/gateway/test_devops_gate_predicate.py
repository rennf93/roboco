"""The DevOps infra-review-gate predicate (Stage 3).

``infra_gate_applies`` is the single predicate both the pr_pass precondition
and the dispatcher slot consult. True only when ALL hold:

- ``ROBOCO_DEVOPS_ENABLED`` is ON (enforcement is flag-gated: without the
  flag nothing can spawn devops-1, so requiring its verdict would wedge
  every infra PR);
- the changed files intersect the project's effective infra globs (the
  declaration read itself is conventions-flag-independent by contract);
- the task is NOT devops-authored (self-review exclusion).

The zero-commit ``pr_waived`` waiver needs no predicate branch: a waived task
reroutes to ``submit_pm_review`` at gate entry and never reaches
``awaiting_pr_review`` at all (pinned in test_verb_runner.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib

# ---------------------------------------------------------------------------
# The pure glob matcher
# ---------------------------------------------------------------------------


def test_infra_glob_hit_exact_and_fnmatch() -> None:
    assert devops_gate_lib.infra_glob_hit("Dockerfile", "Dockerfile*")
    assert devops_gate_lib.infra_glob_hit("Dockerfile.dev", "Dockerfile*")
    assert devops_gate_lib.infra_glob_hit(
        ".github/workflows/ci.yml", ".github/workflows/**"
    )
    assert devops_gate_lib.infra_glob_hit("compose.yaml", "compose*.y*ml")


def test_infra_glob_miss() -> None:
    assert not devops_gate_lib.infra_glob_hit("roboco/services/task.py", "docker/**")
    assert not devops_gate_lib.infra_glob_hit("panel/src/app.tsx", "Dockerfile*")


def test_changed_files_hit_infra_any_intersection() -> None:
    files = ["roboco/services/task.py", "docker/Dockerfile", "panel/x.tsx"]
    assert devops_gate_lib.changed_files_hit_infra(files, ["docker/**"])
    assert not devops_gate_lib.changed_files_hit_infra(files, ["k8s/**"])
    assert not devops_gate_lib.changed_files_hit_infra(files, [])


# ---------------------------------------------------------------------------
# The verdict record reader
# ---------------------------------------------------------------------------


def test_devops_verdict_record_reads_valid_payload() -> None:
    t = MagicMock(
        notes_structured={
            "devops_review": {"verdict": "passed", "head_sha": "h1", "summary": "ok"}
        }
    )
    record = devops_gate_lib.devops_verdict_record(t)
    assert record is not None
    assert record["head_sha"] == "h1"


def test_devops_verdict_record_none_for_absent_or_malformed() -> None:
    assert devops_gate_lib.devops_verdict_record(MagicMock(notes_structured={})) is None
    assert (
        devops_gate_lib.devops_verdict_record(MagicMock(notes_structured=None)) is None
    )
    assert (
        devops_gate_lib.devops_verdict_record(
            MagicMock(notes_structured={"devops_review": "garbage"})
        )
        is None
    )
    assert (
        devops_gate_lib.devops_verdict_record(
            MagicMock(notes_structured={"devops_review": {"summary": "no verdict"}})
        )
        is None
    )


def test_verdict_satisfies_head_match_and_rearm() -> None:
    record = {"verdict": "passed", "head_sha": "h1"}
    assert devops_gate_lib.verdict_satisfies_head(record, "h1")
    # A failed verdict never satisfies, whatever the head.
    assert not devops_gate_lib.verdict_satisfies_head(
        {"verdict": "failed", "head_sha": "h1"}, "h1"
    )
    # Head advanced -> the verdict expired (the re-arm).
    assert not devops_gate_lib.verdict_satisfies_head(record, "h2")
    # Head capture failed -> fail open, not wedge.
    assert devops_gate_lib.verdict_satisfies_head(record, None)


# ---------------------------------------------------------------------------
# infra_gate_applies: flag / glob / self-review composition
# ---------------------------------------------------------------------------


def _task(**over: Any) -> MagicMock:
    base: dict[str, Any] = {
        "id": uuid4(),
        "assigned_to": None,
        "status": "awaiting_pr_review",
    }
    base.update(over)
    return MagicMock(**base)


def _svc(descendants: list[Any] | None = None) -> AsyncMock:
    svc = AsyncMock()
    svc.session = AsyncMock()
    svc.get_all_descendants = AsyncMock(return_value=descendants or [])
    return svc


@pytest.fixture
def stub_globs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Pin the conventions read; the accessor itself has Stage-2 coverage."""
    globs = ["docker/**", "Dockerfile*"]
    monkeypatch.setattr(
        devops_gate_lib, "effective_infra_globs_for_task", AsyncMock(return_value=globs)
    )
    return globs


@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_flag_off_requires_nothing_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    globs_reader = AsyncMock(return_value=["docker/**"])
    monkeypatch.setattr(devops_gate_lib, "effective_infra_globs_for_task", globs_reader)
    t = _task()
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(), t, changed_files=["docker/Dockerfile"]
    )
    # The flag check short-circuits BEFORE any conventions read.
    globs_reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_flag_on_glob_hit_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    assert await devops_gate_lib.infra_gate_applies(
        _svc(), _task(), changed_files=["panel/a.tsx", "docker/Dockerfile"]
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_flag_on_glob_miss_does_not_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(), _task(), changed_files=["roboco/services/task.py", "panel/a.tsx"]
    )


@pytest.mark.asyncio
async def test_empty_globs_opt_out_project_does_not_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    monkeypatch.setattr(
        devops_gate_lib, "effective_infra_globs_for_task", AsyncMock(return_value=[])
    )
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(), _task(), changed_files=["docker/Dockerfile"]
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_empty_changed_files_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    globs_reader = AsyncMock(return_value=["docker/**"])
    monkeypatch.setattr(devops_gate_lib, "effective_infra_globs_for_task", globs_reader)
    # A fetch failure yields [] - the gate fails open and never reads globs.
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(), _task(), changed_files=[]
    )
    globs_reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_self_review_exclusion_task_assigned_to_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = devops_gate_lib.devops_agent_id()
    assert devops_id is not None
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(), _task(assigned_to=devops_id), changed_files=["docker/Dockerfile"]
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_self_review_exclusion_code_descendant_authored_by_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = devops_gate_lib.devops_agent_id()
    leaf = MagicMock(task_type="code", assigned_to=devops_id)
    assert not await devops_gate_lib.infra_gate_applies(
        _svc(descendants=[leaf]),
        _task(),
        changed_files=["docker/Dockerfile"],
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("stub_globs")
@pytest.mark.asyncio
async def test_non_code_descendant_assigned_to_devops_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only CODE-type descendants count as authorship - devops-1 co-claiming
    or being assigned an advisory row on the chain is not authorship."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = devops_gate_lib.devops_agent_id()
    advisory = MagicMock(task_type="planning", assigned_to=devops_id)
    assert await devops_gate_lib.infra_gate_applies(
        _svc(descendants=[advisory]),
        _task(),
        changed_files=["docker/Dockerfile"],
    )
