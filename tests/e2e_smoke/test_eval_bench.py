"""Integration test for the eval bench's own orchestration/scoring plumbing.

The real ``StageSpawner`` (``OrchestratorStageSpawner``) drives a REAL agent
container via ``AgentOrchestrator.spawn_agent`` and needs a Docker daemon +
built agent images — it cannot run here (see ``roboco/eval/runner.py``'s
module docstring). This test substitutes a scripted stand-in that drives the
SAME real MCP flow/do tool functions ``tests.e2e_smoke.harness.ScriptedAgent``
uses (via the existing ``dev_arc`` / ``qa_arc`` / ``doc_arc`` helpers, plus a
PM ``complete`` call) so it proves the runner's OWN code — its throwaway-DB +
disposable-project setup, its status-driven stage loop, its PM pre-claim,
its deterministic scoring, its JSON/table output — without touching Docker.

Runs the smallest fixture (a single-file bug fix) end to end: PENDING ->
awaiting_qa -> awaiting_documentation -> awaiting_pm_review -> completed.

The QA and PM scripted arcs (``_qa_defect_catch_arc`` /
``_pm_delegate_arc``) prove the runner's plumbing for the new role entry
points (QA enters at ``awaiting_qa`` with a pre-built PR; PM enters at
``pending`` as a parent task) without Docker — the scripted spawner drives
the SAME real MCP flow verbs a real container would.

Gating: like every other module here, this is skipped unless
``ROBOCO_E2E_SMOKE=1`` (see ``tests/e2e_smoke/conftest.py``'s
``pytest_collection_modifyitems``) — it needs the real test Postgres, which
``EvalRunner`` provisions its own throwaway copy of (see
``roboco/eval/runner.py``'s ``_scratch_database``), independent of this
package's shared session-scoped ``e2e_stack`` fixture.
"""

from __future__ import annotations

import asyncio
import types
from typing import TYPE_CHECKING, Any
from uuid import UUID

from roboco.config import settings
from roboco.eval.fixtures import FIXTURES
from roboco.eval.runner import BenchJudge, EvalRunner, JudgeVerdict, _bench_environment
from tests.e2e_smoke.arcs import Company, dev_arc, doc_arc, qa_arc
from tests.e2e_smoke.harness import ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    import pytest
    from roboco.eval.fixtures import BenchTaskSpec
    from tests.e2e_smoke.harness import E2EStack

_FIXTURE_KEY = "bugfix-off-by-one"
# IWillPlanRequest.approach enforces min_length=150 — the scripted PM arc
# builds an approach well above that floor; this constant keeps the
# defensive assertion readable without a magic-value lint violation.
_MIN_APPROACH_LEN = 150
_FIXED_FIX = (
    "def paginate(items, page, size):\n"
    "    start = (page - 1) * size\n"
    "    end = page * size\n"
    "    return items[start:end]\n"
)
# The buggy code IS the injected defect for QA fixtures — the slice end is
# `page * size - 1` instead of `page * size`, dropping the last item.
_DEFECT_DESCRIPTION = (
    "paginate.py line 3: the slice end `page * size - 1` drops the last "
    "item of every page — should be `page * size` per the acceptance "
    "criteria."
)


def _fixture() -> BenchTaskSpec:
    for f in FIXTURES:
        if f.key == _FIXTURE_KEY:
            return f
    raise AssertionError(f"{_FIXTURE_KEY!r} fixture not found in FIXTURES")


def _qa_fixture() -> Any:
    """A QA-entry fixture: same repo_files as the bugfix fixture (the buggy
    code IS the injected defect), but ``target_role='qa'`` and
    ``entry_status='awaiting_qa'`` so the runner pre-advances the task to
    awaiting_qa with a pre-built PR instead of seeding master and starting
    at pending."""
    base = _fixture()
    return types.SimpleNamespace(
        key=base.key,
        title=base.title,
        description=base.description,
        acceptance_criteria=base.acceptance_criteria,
        task_type=base.task_type,
        nature=base.nature,
        repo_files=base.repo_files,
        expectations=base.expectations,
        target_role="qa",
        entry_status="awaiting_qa",
        injected_defect=_DEFECT_DESCRIPTION,
        is_parent=False,
        expected_coverage=(),
    )


def _pm_fixture() -> Any:
    """A PM parent-task fixture: the PM must delegate with
    ``covers_parent_criteria`` mapping every acceptance criterion."""
    base = _fixture()
    return types.SimpleNamespace(
        key=base.key,
        title=base.title,
        description=base.description,
        acceptance_criteria=base.acceptance_criteria,
        task_type=base.task_type,
        nature=base.nature,
        repo_files=base.repo_files,
        expectations=base.expectations,
        target_role="cell_pm",
        entry_status="pending",
        injected_defect=None,
        is_parent=True,
        expected_coverage=tuple(base.acceptance_criteria),
    )


def _stub_company() -> Company:
    """A ``Company`` carrying the FIXED uuids ``EvalRunner``'s own company
    seeding uses (not fresh random ones, unlike ``arcs.seed_company``) — the
    "be-*" slugs are hardcoded inside ``dev_arc`` / ``qa_arc`` / ``doc_arc``
    themselves, so this only needs to supply the matching ids."""
    from roboco.foundation import identity as _foundation

    company = Company()
    company.dev_id = _foundation.AGENTS["be-dev-1"].uuid
    company.qa_id = _foundation.AGENTS["be-qa"].uuid
    company.doc_id = _foundation.AGENTS["be-doc"].uuid
    company.cell_pm_id = _foundation.AGENTS["be-pm"].uuid
    return company


# ---------------------------------------------------------------------------
# Scripted arcs for the new role entry points
# ---------------------------------------------------------------------------


def _qa_defect_catch_arc(stack: E2EStack, company: Company, task_id: Any) -> None:
    """QA defect-catch: the QA agent reviews a pre-built PR with an injected
    defect and fails the review — proving the runner's QA entry plumbing
    (task pre-advanced to awaiting_qa, QA claims + fails with findings)."""
    tid = str(task_id)
    qa = ScriptedAgent(stack, company.qa_id, "be-qa", "qa")
    expect_ok(qa.flow("claim_review", task_id=tid), "qa claim_review")
    expect_ok(
        qa.do(
            "note",
            scope="learning",
            task_id=tid,
            text=(
                "Review learning: the PR diff contains an off-by-one defect "
                "in the slice bound — the end is `page * size - 1` instead of "
                "`page * size`, dropping the last item of every page."
            ),
        ),
        "qa learning note",
    )
    expect_ok(
        qa.flow(
            "fail_review",
            task_id=tid,
            issues=[
                "paginate.py: the slice end `page * size - 1` drops the last "
                "item of every page — should be `page * size` per the "
                "acceptance criteria."
            ],
        ),
        "qa fail_review",
    )


def _pm_delegate_arc(
    stack: E2EStack, company: Company, task_id: Any, agent_slug: str
) -> None:
    """PM delegate-with-coverage: the PM plans the parent task and delegates
    to a developer child with ``covers_parent_criteria`` mapping every
    acceptance criterion — proving the runner's PM parent-task entry
    plumbing (task at pending, PM plans + delegates in one turn)."""
    from sqlalchemy import select

    tid = str(task_id)
    pm = ScriptedAgent(stack, company.cell_pm_id, agent_slug, "cell_pm")

    env = expect_ok(pm.flow("give_me_work"), "pm give_me_work")
    assert env.get("task_id") == tid, f"expected task {tid}, got: {env}"

    # Read the parent's acceptance criteria for covers_parent_criteria.
    async def _read_crits(session: Any) -> list[str]:
        from roboco.db.tables import TaskTable

        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return list(row.acceptance_criteria or [])

    criteria: list[str] = stack.run_db(_read_crits)

    approach = (
        "Plan and delegate the paginate off-by-one fix to a backend developer: "
        "the child writes the corrected slice bound, commits, opens the PR, and "
        "self-verifies every acceptance criterion. Single-file change, low risk, "
        "no cross-cell dependencies."
    )
    assert len(approach) >= _MIN_APPROACH_LEN, (
        f"approach too short ({len(approach)} chars)"
    )

    def _plan() -> dict[str, Any]:
        return pm.flow(
            "i_will_plan",
            task_id=tid,
            plan="Delegate the paginate fix to a backend developer child.",
            approach=approach,
            sub_tasks=[
                {
                    "title": "Fix the off-by-one slice bound in paginate()",
                    "description": (
                        "Change the slice end from `page * size - 1` to "
                        "`page * size` in paginate.py so every item appears "
                        "exactly once across all pages."
                    ),
                }
            ],
        )

    # The claim-time tracing gate demands a note before i_will_plan succeeds
    # — same pattern as dev_arc's i_will_work_on retry.
    expect_error(_plan(), "tracing_gap", "pm first i_will_plan")
    expect_ok(
        pm.do(
            "note",
            scope="note",
            task_id=tid,
            text=(
                "Initial assessment: a single-file bug fix delegated to one "
                "backend developer child; the off-by-one slice bound is the "
                "root cause and the fix is a one-line change."
            ),
        ),
        "pm note at claim",
    )
    expect_ok(_plan(), "pm i_will_plan retry")

    # Delegate with covers_parent_criteria mapping every acceptance criterion.
    expect_ok(
        pm.flow(
            "delegate",
            parent_task_id=tid,
            title="Fix off-by-one in paginate()",
            description=(
                "Fix the slice bound in paginate.py so the last item of "
                "every page is included."
            ),
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            estimated_complexity="low",
            acceptance_criteria=[
                "paginate(list(range(10)), page=1, size=3) returns [0, 1, 2]",
            ],
            covers_parent_criteria=criteria,
        ),
        "pm delegate with coverage",
    )


class _ScriptedBenchSpawner:
    """Test-only ``StageSpawner``: applies the KNOWN correct fix via the real
    MCP flow/do tool functions, standing in for a real container spawn.

    ``dev_arc`` / ``qa_arc`` / ``doc_arc`` (and ``ScriptedAgent`` itself) call
    ``E2EStack.run_db``, which runs its own ``asyncio.run()`` per call — fine
    from a plain sync pytest test, but ``run_stage`` is awaited from inside
    ``_drive_task_to_terminal``'s own event loop, where a nested
    ``asyncio.run()`` raises. Running the scripted turn on a worker thread
    (``asyncio.to_thread``) gives it a thread with no running loop, exactly
    like the sync test functions those helpers were written for.

    ``target_role`` selects which scripted arc to run for a given role:
    ``developer`` (default) uses the existing dev→QA→doc→PM-complete flow;
    ``qa`` uses the defect-catch arc (``fail_review``); ``cell_pm`` uses the
    delegate-with-coverage arc (``i_will_plan`` + ``delegate``).
    """

    def __init__(self, stack: E2EStack, *, target_role: str = "developer") -> None:
        self._stack = stack
        self._company = _stub_company()
        self._target_role = target_role

    async def run_stage(self, *, task: dict[str, Any], agent_slug: str) -> None:
        await asyncio.to_thread(self._run_stage_sync, task, agent_slug)

    def _run_stage_sync(self, task: dict[str, Any], agent_slug: str) -> None:
        from roboco.agents_config import get_agent_role

        role = get_agent_role(agent_slug)
        task_id = UUID(task["id"])
        status = task.get("status")
        if role == "developer":
            dev_arc(
                self._stack,
                self._company,
                task["project_slug"],
                task_id,
                work=(f"bench/{_FIXTURE_KEY}/paginate.py", _FIXED_FIX),
            )
        elif role == "qa":
            if self._target_role == "qa" and status == "awaiting_qa":
                # QA entry: the task was pre-advanced to awaiting_qa with a
                # pre-built PR containing the injected defect — the QA agent
                # catches it and fails the review.
                _qa_defect_catch_arc(self._stack, self._company, task_id)
            else:
                # Developer flow: QA passes the (correct) PR.
                qa_arc(self._stack, self._company, task_id)
        elif role == "documenter":
            doc_arc(
                self._stack,
                self._company,
                task_id,
                filename=f"bench/{_FIXTURE_KEY}/paginate.py",
            )
        elif role == "cell_pm":
            if self._target_role == "cell_pm" and status in (
                "pending",
                "claimed",
                "in_progress",
            ):
                # PM entry: the task is a parent at pending/in_progress —
                # the PM plans and delegates with covers_parent_criteria.
                _pm_delegate_arc(self._stack, self._company, task_id, agent_slug)
            else:
                # Developer flow: PM completes the review.
                pm = ScriptedAgent(
                    self._stack, self._company.cell_pm_id, agent_slug, "cell_pm"
                )
                pm.flow(
                    "complete",
                    task_id=str(task_id),
                    notes="Scripted bench completion: QA passed, docs complete.",
                )
        else:
            raise AssertionError(f"unexpected role for the scripted bench: {role!r}")


_EXPECTED_JUDGE_SCORE = 5


class _FakeJudge(BenchJudge):
    """Deterministic stand-in for the local-model judge — no network."""

    async def score(
        self, *, fixture: BenchTaskSpec, diff: str, notes: str
    ) -> JudgeVerdict:
        return JudgeVerdict(
            score=_EXPECTED_JUDGE_SCORE, rationale="scripted test: assumed correct"
        )


def test_eval_runner_drives_a_fixture_to_completion_with_a_scripted_spawn() -> None:
    runner = EvalRunner(
        make_spawner=_ScriptedBenchSpawner,
        judge=_FakeJudge(),
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "be-dev-1", "scripted-test", fixtures=[_fixture()], json_out=None
    )

    assert cohort.role_slug == "be-dev-1"
    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.fixture_key == _FIXTURE_KEY
    assert result.metrics.final_status == "completed"
    assert result.metrics.stalled is False
    assert result.passed is True
    assert result.metrics.revision_count == 0
    assert result.judge.score == _EXPECTED_JUDGE_SCORE
    assert cohort.pass_rate == 1.0
    # No real container spawned, so no agent_spawn_sessions rows accrued for
    # this task — the scripted stand-in proves the runner's DB/polling/
    # scoring plumbing, not token/cost accounting (that needs a real spawn;
    # see the module docstring).
    assert result.metrics.total_tokens == 0
    assert result.metrics.estimated_cost_usd == 0.0


def test_eval_runner_drives_qa_fixture_with_defect_catch() -> None:
    """QA entry: the task is pre-advanced to awaiting_qa with a pre-built PR
    containing the injected defect (the buggy slice bound). The scripted QA
    agent catches the defect and fails the review — proving the runner's
    QA entry plumbing (pre-advancement, claim_review, fail_review) without
    Docker."""
    runner = EvalRunner(
        make_spawner=lambda stack: _ScriptedBenchSpawner(stack, target_role="qa"),
        judge=_FakeJudge(),
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "be-qa", "scripted-qa-test", fixtures=[_qa_fixture()], json_out=None
    )

    assert cohort.role_slug == "be-qa"
    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.fixture_key == _FIXTURE_KEY
    # QA caught the defect → fail_review → needs_revision.
    assert result.metrics.final_status == "needs_revision"
    assert result.metrics.stalled is True  # max_stages=1 stops after the QA turn
    assert result.metrics.revision_count == 1  # fail_review increments it
    # The task didn't reach completed, so passed is False — the judge score
    # is what differentiates a good QA review from a bad one.
    assert result.passed is False


def test_eval_runner_drives_pm_fixture_with_delegation() -> None:
    """PM entry: the task is a parent at pending, assigned to the cell PM.
    The scripted PM plans and delegates with ``covers_parent_criteria``
    mapping every acceptance criterion — proving the runner's PM parent-task
    entry plumbing (parent task, i_will_plan, delegate with coverage) without
    Docker."""
    runner = EvalRunner(
        make_spawner=lambda stack: _ScriptedBenchSpawner(stack, target_role="cell_pm"),
        judge=_FakeJudge(),
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "be-pm", "scripted-pm-test", fixtures=[_pm_fixture()], json_out=None
    )

    assert cohort.role_slug == "be-pm"
    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.fixture_key == _FIXTURE_KEY
    # PM planned + delegated → task stays at in_progress (the child hasn't run).
    assert result.metrics.final_status == "in_progress"
    assert result.metrics.stalled is True  # max_stages=1 stops after the PM turn
    assert result.metrics.revision_count == 0  # no reviews happened
    assert result.passed is False  # not completed


def test_bench_environment_disables_vault_writes_even_when_ambient_flags_are_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bench run must never write into the operator's REAL Obsidian vault.
    Simulates the compose-default posture (every vault flag armed True) and
    asserts `_bench_environment` forces them all off for its duration, then
    restores the prior values on exit — the exact leak an adversarial review
    flagged (TaskService.create / JournalService / A2AService all gate on
    obsidian_vault_enabled first, so patching it is the load-bearing part;
    the three sub-flags are patched too for defense-in-depth)."""
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    monkeypatch.setattr(settings, "vault_intake_enabled", True)
    monkeypatch.setattr(settings, "vault_kb_enabled", True)
    monkeypatch.setattr(settings, "vault_report_enabled", True)
    armed = (
        settings.obsidian_vault_enabled,
        settings.vault_intake_enabled,
        settings.vault_kb_enabled,
        settings.vault_report_enabled,
    )

    with _bench_environment("be-dev-1"):
        assert settings.obsidian_vault_enabled is False
        assert settings.vault_intake_enabled is False
        assert settings.vault_kb_enabled is False
        assert settings.vault_report_enabled is False

    # Restored to the (simulated ambient) armed state once the bench exits.
    restored = (
        settings.obsidian_vault_enabled,
        settings.vault_intake_enabled,
        settings.vault_kb_enabled,
        settings.vault_report_enabled,
    )
    assert restored == armed == (True, True, True, True)


def test_bench_environment_disables_vault_writes_for_qa_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vault-flag isolation must also cover the non-developer role entry
    points (QA enters at awaiting_qa, not pending). Same posture as the
    developer test: every vault flag armed True, asserts all forced off
    inside ``_bench_environment('be-qa')``, restored on exit."""
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    monkeypatch.setattr(settings, "vault_intake_enabled", True)
    monkeypatch.setattr(settings, "vault_kb_enabled", True)
    monkeypatch.setattr(settings, "vault_report_enabled", True)
    armed = (
        settings.obsidian_vault_enabled,
        settings.vault_intake_enabled,
        settings.vault_kb_enabled,
        settings.vault_report_enabled,
    )

    with _bench_environment("be-qa"):
        assert settings.obsidian_vault_enabled is False
        assert settings.vault_intake_enabled is False
        assert settings.vault_kb_enabled is False
        assert settings.vault_report_enabled is False

    restored = (
        settings.obsidian_vault_enabled,
        settings.vault_intake_enabled,
        settings.vault_kb_enabled,
        settings.vault_report_enabled,
    )
    assert restored == armed == (True, True, True, True)
