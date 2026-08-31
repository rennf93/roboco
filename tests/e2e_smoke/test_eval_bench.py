"""Integration test for the eval bench's own orchestration/scoring plumbing.

The real ``StageSpawner`` (``OrchestratorStageSpawner``) drives a REAL agent
container via ``AgentOrchestrator.spawn_agent`` and needs a Docker daemon +
built agent images — it cannot run here (see ``roboco/eval/runner.py``'s
module docstring). This test substitutes a scripted stand-in that drives the
SAME real MCP flow/do tool functions ``tests.e2e_smoke.harness.ScriptedAgent``
uses (via the existing ``dev_arc`` / ``doc_arc`` helpers, a purpose-built QA
``fail_review`` defect-catch arc, and a PM ``i_will_plan``-``delegate``
coverage arc) so it
proves the runner's OWN code — its throwaway-DB + disposable-project setup,
its status-driven stage loop, its entry-point prebuilds, its PM pre-claim,
its deterministic scoring, its JSON/table output — without touching Docker.

Covered entry shapes (one per role cohort):

* developer: PENDING -> awaiting_qa -> awaiting_documentation ->
  awaiting_pm_review -> completed (the original happy path).
* qa: the runner pre-builds a defective PR (the fixture's repo files are the
  "fix attempt", never seeded to master) and parks the task at awaiting_qa;
  the scripted QA catches the injected defect via ``fail`` → needs_revision
  (the deterministic pass for a trap fixture) with revision_count bumped.
* cell_pm: a PARENT fixture task under the bench cell; the scripted PM
  claims it (``i_will_plan``, claim-time ``journal:decision``, quick_context)
  and delegates one child with ``covers_parent_criteria`` mapping EVERY
  parent acceptance criterion — the drive loop's designed stop.
* main_pm: the same delegation arc against a ROOT-level fixture task in the
  explicit non-cell bench environment (no bench cell is seeded at all).

Gating: like every other module here, this is skipped unless
``ROBOCO_E2E_SMOKE=1`` (see ``tests/e2e_smoke/conftest.py``'s
``pytest_collection_modifyitems``) — it needs the real test Postgres, which
``EvalRunner`` provisions its own throwaway copy of (see
``roboco/eval/runner.py``'s ``_scratch_database``), independent of this
package's shared session-scoped ``e2e_stack`` fixture.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from roboco.config import settings
from roboco.eval.fixtures import FIXTURES, BenchTaskSpec
from roboco.eval.runner import (
    BenchJudge,
    EvalRunner,
    JudgeVerdict,
    _bench_environment,
)
from roboco.models.base import TaskNature, TaskType
from tests.e2e_smoke.arcs import Company, dev_arc, doc_arc
from tests.e2e_smoke.harness import ScriptedAgent, expect_error, expect_ok

if TYPE_CHECKING:
    import pytest
    from tests.e2e_smoke.harness import E2EStack

_FIXTURE_KEY = "bugfix-off-by-one"
_FIXED_FIX = (
    "def paginate(items, page, size):\n"
    "    start = (page - 1) * size\n"
    "    end = page * size\n"
    "    return items[start:end]\n"
)


def _fixture() -> BenchTaskSpec:
    for f in FIXTURES:
        if f.key == _FIXTURE_KEY:
            return f
    raise AssertionError(f"{_FIXTURE_KEY!r} fixture not found in FIXTURES")


_QA_FIXTURE_KEY = "qa-trap-off-by-one"
# The PR content a QA bench fixture puts under review: a plausible-looking
# "fix" whose slice end STILL drops the final item — the injected defect a
# good QA catches (fail) and a bad one misses (pass).
_DEFECTIVE_FIX = (
    "def paginate(items, page, size):\n"
    "    start = (page - 1) * size\n"
    "    # fix: include every item across all pages\n"
    "    end = page * size - 1\n"
    "    return items[start:end]\n"
)


def _qa_fixture() -> BenchTaskSpec:
    return BenchTaskSpec(
        key=_QA_FIXTURE_KEY,
        title="QA review: off-by-one fix in paginate()",
        description=(
            "A developer PR claims to fix `bench/qa-trap-off-by-one/paginate."
            "py`'s off-by-one so every item appears exactly once across all "
            "pages. Review the PR diff: does the fix actually make the final "
            "page include its last item?"
        ),
        acceptance_criteria=(
            "paginate(list(range(10)), page=1, size=3) returns [0, 1, 2]",
            "paginate(list(range(10)), page=4, size=3) returns [9] (the "
            "last, previously-dropped item)",
            "No item is duplicated or skipped across pages 1..4 for size=3",
        ),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        repo_files=(("bench/qa-trap-off-by-one/paginate.py", _DEFECTIVE_FIX),),
        expectations=(
            "A good QA FAILS this PR: the committed 'fix' keeps the "
            "page*size-1 slice end, so paginate(..., page=4, size=3) still "
            "returns [] instead of [9]. A QA that passes it missed the "
            "injected defect."
        ),
        target_role="qa",
    )


_PM_FIXTURE_KEY = "pm-plan-greeting"


def _pm_fixture(target_role: str) -> BenchTaskSpec:
    """A planning parent whose deliverable is the delegation: one child with
    ``covers_parent_criteria`` mapping every acceptance criterion. Reused for
    the cell_pm and main_pm cohorts (only ``target_role`` differs)."""
    return BenchTaskSpec(
        key=f"{target_role}-{_PM_FIXTURE_KEY}",
        title="Plan the greeting-helper delivery",
        description=(
            "Planning parent: decompose the greeting-helper work into an "
            "implementation child covering every acceptance criterion of "
            "this parent, and delegate it to the implementing team."
        ),
        acceptance_criteria=(
            "greet('Ada') == 'Hi, Ada!'",
            "greet('Ada', formal=True) == 'Good day, Ada.'",
            "greet('') and greet('   ') both raise ValueError",
        ),
        task_type=TaskType.PLANNING,
        nature=TaskNature.TECHNICAL,
        repo_files=(),
        expectations=(
            "The PM delegates with covers_parent_criteria mapping EVERY "
            "acceptance criterion of the parent — none dropped or left "
            "unmapped."
        ),
        target_role=target_role,
    )


def _stub_company() -> Company:
    """A ``Company`` carrying the FIXED uuids ``EvalRunner``'s own company
    seeding uses (not fresh random ones, unlike ``arcs.seed_company``) — the
    "be-*" / "main-pm" slugs are hardcoded inside the scripted arcs
    themselves, so this only needs to supply the matching ids."""
    from roboco.foundation import identity as _foundation

    company = Company()
    company.dev_id = _foundation.AGENTS["be-dev-1"].uuid
    company.qa_id = _foundation.AGENTS["be-qa"].uuid
    company.doc_id = _foundation.AGENTS["be-doc"].uuid
    company.cell_pm_id = _foundation.AGENTS["be-pm"].uuid
    company.main_pm_id = _foundation.AGENTS["main-pm"].uuid
    return company


class _ScriptedBenchSpawner:
    """Test-only ``StageSpawner``: applies the KNOWN correct turn for the
    role under test via the real MCP flow/do tool functions, standing in for
    a real container spawn.

    ``dev_arc`` / ``qa_arc`` / ``doc_arc`` (and ``ScriptedAgent`` itself) call
    ``E2EStack.run_db``, which runs its own ``asyncio.run()`` per call — fine
    from a plain sync pytest test, but ``run_stage`` is awaited from inside
    ``_drive_task_to_terminal``'s own event loop, where a nested
    ``asyncio.run()`` raises. Running the scripted turn on a worker thread
    (``asyncio.to_thread``) gives it a thread with no running loop, exactly
    like the sync test functions those helpers were written for.
    """

    def __init__(self, stack: E2EStack, fixture: BenchTaskSpec | None = None) -> None:
        self._stack = stack
        self._fixture = fixture if fixture is not None else _fixture()
        self._company = _stub_company()

    async def run_stage(self, *, task: dict[str, Any], agent_slug: str) -> None:
        await asyncio.to_thread(self._run_stage_sync, task, agent_slug)

    def _criteria(self, task_id: str) -> list[str]:
        from roboco.db.tables import TaskTable
        from sqlalchemy import select

        async def _run(session: Any) -> list[str]:
            row = (
                await session.execute(
                    select(TaskTable).where(TaskTable.id == UUID(task_id))
                )
            ).scalar_one()
            return list(row.acceptance_criteria or [])

        return cast("list[str]", self._stack.run_db(_run))

    def _run_stage_sync(self, task: dict[str, Any], agent_slug: str) -> None:
        from roboco.agents_config import get_agent_role

        role = get_agent_role(agent_slug)
        task_id = UUID(task["id"])
        tid = task["id"]
        if role == "developer":
            dev_arc(
                self._stack,
                self._company,
                task["project_slug"],
                task_id,
                work=(self._fixture.repo_files[0][0], _FIXED_FIX),
            )
        elif role == "qa":
            qa = ScriptedAgent(self._stack, self._company.qa_id, agent_slug, "qa")
            expect_ok(qa.flow("claim_review", task_id=tid), "qa claim_review")
            criteria = self._criteria(tid)
            expect_ok(
                qa.do(
                    "note",
                    scope="learning",
                    task_id=tid,
                    text=(
                        "Review learning: walked the PR diff criterion by "
                        "criterion — the committed slice end is still "
                        "page*size-1, so the last-item page stays empty and "
                        "the diff violates the acceptance criteria."
                    ),
                ),
                "qa learning note",
            )
            expect_ok(
                qa.flow(
                    "fail_review",
                    task_id=tid,
                    findings=[
                        {
                            "file": self._fixture.repo_files[0][0],
                            "line": 4,
                            "severity": "blocker",
                            "criterion": criteria[-1],
                            "expected": (
                                "slice end page * size, so page 4 returns [9]"
                            ),
                            "actual": (
                                "slice end still page * size - 1; page 4 "
                                "returns [] and the last item is dropped"
                            ),
                            "fix": "change the slice end to page * size",
                            "evidence": "PR diff hunk: `end = page * size - 1`",
                        }
                    ],
                ),
                "qa fail_review catches the injected defect",
            )
        elif role == "documenter":
            # doc_arc takes (stack, company, task_id) — no project_slug.
            doc_arc(
                self._stack,
                self._company,
                task_id,
                filename=self._fixture.repo_files[0][0],
            )
        elif role in ("cell_pm", "main_pm"):
            self._pm_delegate_arc(tid, role)
        else:
            raise AssertionError(f"unexpected role for the scripted bench: {role!r}")

    def _pm_delegate_arc(self, tid: str, role: str) -> None:
        """The PM bench arc for fixture.target_role in {cell_pm, main_pm}:
        claim the PARENT task (i_will_plan + the claim-time journal:decision
        the tracing gate demands), fill quick_context (delegate's resumption
        gate), then delegate one child whose ``covers_parent_criteria`` maps
        EVERY parent acceptance criterion."""
        fixture = self._fixture
        if role == "cell_pm":
            pm = ScriptedAgent(
                self._stack, self._company.cell_pm_id, "be-pm", "cell_pm"
            )
        elif role == "main_pm":
            pm = ScriptedAgent(
                self._stack, self._company.main_pm_id, "main-pm", "main_pm"
            )
        else:
            raise AssertionError(f"unexpected PM role: {role!r}")

        def _plan() -> dict[str, Any]:
            return pm.flow(
                "i_will_plan",
                task_id=tid,
                plan=(
                    f"Plan {fixture.title}: decompose the parent's acceptance "
                    "criteria into one implementation slice and delegate it "
                    "with covers_parent_criteria covering every criterion."
                ),
                approach=(
                    "Decompose the parent into a single backend implementation "
                    "child that carries every acceptance criterion via "
                    "covers_parent_criteria, keeping the wave flat and the "
                    "coverage mapping fully verifiable in one delegation turn."
                ),
                sub_tasks=[
                    {
                        "title": f"Implement: {fixture.title}",
                        "description": (
                            "Delegate the implementation child that delivers "
                            "every acceptance criterion of this parent."
                        ),
                    }
                ],
            )

        # Real choreography (mirrors dev_arc's claim-time note): the composed
        # claim succeeds and stays; the post-claim tracing gate demands the
        # claim-time journal:decision; the retry short-circuits as re-entry.
        expect_error(_plan(), "tracing_gap", "pm i_will_plan first attempt")
        expect_ok(
            pm.do(
                "note",
                scope="decision",
                task_id=tid,
                text=(
                    "Decomposition decision: one implementation child with "
                    "full criteria coverage — the parent's acceptance "
                    "criteria fit a single backend slice, so a flat "
                    "single-child wave is the least lossy mapping."
                ),
            ),
            "pm decision note at claim",
        )
        expect_ok(_plan(), "pm i_will_plan retry")

        # delegate requires the parent's quick_context resumption section —
        # the PM handoff's TOP-LEVEL done/next fields (see do_server.note).
        expect_ok(
            pm.do(
                "note",
                scope="handoff",
                task_id=tid,
                text=("Parent claimed and planned; single-child decomposition agreed."),
                done=(
                    "Parent claimed and planned: single-child decomposition "
                    "agreed, criteria mapping resolved."
                ),
                next=(
                    "Delegate the implementation child with full "
                    "covers_parent_criteria mapping; track it to terminal."
                ),
            ),
            "pm quick_context handoff",
        )

        # cell_pm delegates code to its own team's dev; main_pm delegates a
        # planning cell task to a cell PM (the main_pm->cell_pm chain rule).
        expect_ok(
            pm.flow(
                "delegate",
                parent_task_id=tid,
                title=f"Implement: {fixture.title}",
                description=(
                    "Implementation child of the planning parent: delivers "
                    "every acceptance criterion the parent declares; QA and "
                    "docs follow in their own lifecycle phases."
                ),
                assigned_to="be-dev-1" if role == "cell_pm" else "be-pm",
                team="backend",
                task_type="code" if role == "cell_pm" else "planning",
                nature="technical",
                acceptance_criteria=list(fixture.acceptance_criteria),
                estimated_complexity="low",
                covers_parent_criteria=list(fixture.acceptance_criteria),
                intends_to_touch=[f"bench/{fixture.key}/**"],
            ),
            "pm delegate with full coverage",
        )

        children = self._children_of(UUID(tid))
        assert len(children) == 1, f"expected exactly one delegated child: {children}"

    def _children_of(self, task_id: UUID) -> list[dict[str, Any]]:
        from roboco.db.tables import TaskTable
        from sqlalchemy import select

        async def _run(session: Any) -> list[dict[str, Any]]:
            rows = (
                (
                    await session.execute(
                        select(TaskTable).where(TaskTable.parent_task_id == task_id)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {"id": str(r.id), "parent_ac_refs": list(r.parent_ac_refs or [])}
                for r in rows
            ]

        return cast("list[dict[str, Any]]", self._stack.run_db(_run))


_EXPECTED_JUDGE_SCORE = 5


class _FakeJudge(BenchJudge):
    """Deterministic stand-in for the local-model judge — no network.

    Also records the prompt it was handed so tests can pin the role-aware
    template selection (QA vs PM vs developer)."""

    def __init__(self) -> None:
        self.last_fixture_role: str | None = None
        self.last_prompt_head: str | None = None

    async def score(
        self, *, fixture: BenchTaskSpec, diff: str, notes: str
    ) -> JudgeVerdict:
        from roboco.eval.runner import _build_judge_prompt

        self.last_fixture_role = fixture.target_role
        self.last_prompt_head = _build_judge_prompt(fixture, diff, notes)[:220]
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


def test_qa_cohort_reviews_a_prebuilt_defective_pr_and_catches_it() -> None:
    """QA entry point: the runner pre-builds the defective PR and parks the
    task at awaiting_qa; the scripted QA catches the injected defect via
    ``fail`` → needs_revision (the deterministic pass for a trap fixture,
    with revision_count bumped by the bounce)."""
    fixture = _qa_fixture()
    judge = _FakeJudge()
    runner = EvalRunner(
        make_spawner=lambda stack: _ScriptedBenchSpawner(stack, fixture=fixture),
        judge=judge,
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "be-qa", "scripted-qa-trap", fixtures=[fixture], json_out=None
    )

    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.fixture_key == _QA_FIXTURE_KEY
    assert result.metrics.final_status == "needs_revision"
    assert result.metrics.stalled is False
    assert result.metrics.revision_count == 1
    assert result.passed is True
    assert result.judge.score == _EXPECTED_JUDGE_SCORE
    assert cohort.pass_rate == 1.0
    # The grading prompt was the QA catch-vs-miss template, not the generic
    # developer one.
    assert judge.last_fixture_role == "qa"
    assert judge.last_prompt_head is not None
    assert "grading a QA review turn" in judge.last_prompt_head


def test_cell_pm_cohort_delegates_a_parent_with_full_criteria_coverage() -> None:
    """PM entry point (cell_pm): the fixture task is a PARENT; the scripted
    PM claims it and delegates one child mapping every parent AC. The drive
    loop stops at the delegation (in_progress with a live child), and the
    deterministic pass is the coverage check."""
    fixture = _pm_fixture("cell_pm")
    judge = _FakeJudge()
    runner = EvalRunner(
        make_spawner=lambda stack: _ScriptedBenchSpawner(stack, fixture=fixture),
        judge=judge,
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "be-pm", "scripted-cell-pm", fixtures=[fixture], json_out=None
    )

    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.metrics.stalled is False
    assert result.metrics.final_status == "in_progress"
    assert result.passed is True
    assert result.judge.score == _EXPECTED_JUDGE_SCORE
    # The grading prompt was the PM coverage template and named the delegated
    # child (the "diff" slot carries the delegation record for PM fixtures).
    assert judge.last_fixture_role == "cell_pm"
    assert judge.last_prompt_head is not None
    assert "grading a PM planning turn" in judge.last_prompt_head
    assert "Implement: Plan the greeting-helper delivery" in judge.last_prompt_head


def test_main_pm_cohort_plans_a_root_task_in_the_non_cell_environment() -> None:
    """main_pm has NO cell team: the bench environment must run the explicit
    non-cell path (no bench cell seeded; the fixture task is a fresh root
    with Team.MAIN_PM) and still produce a scored, coverage-complete run."""
    fixture = _pm_fixture("main_pm")
    runner = EvalRunner(
        make_spawner=lambda stack: _ScriptedBenchSpawner(stack, fixture=fixture),
        judge=_FakeJudge(),
        fixture_timeout_seconds=60.0,
    )

    cohort = runner.run_cohort(
        "main-pm", "scripted-main-pm", fixtures=[fixture], json_out=None
    )

    assert len(cohort.fixtures) == 1
    result = cohort.fixtures[0]
    assert result.metrics.stalled is False
    assert result.metrics.final_status == "in_progress"
    assert result.passed is True


def test_run_cohort_still_refuses_unbenchable_roles() -> None:
    runner = EvalRunner(judge=_FakeJudge())
    try:
        runner.run_cohort("ceo", "scripted-nope", fixtures=[], json_out=None)
    except ValueError as exc:
        assert "ceo" in str(exc)
    else:
        raise AssertionError("run_cohort should refuse non-bench roles")


def test_bench_environment_disables_vault_writes_even_when_ambient_flags_are_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bench run must never write into the operator's REAL Obsidian vault.
    Simulates the compose-default posture (every vault flag armed True) and
    asserts `_bench_environment` forces them all off for its whole duration
    — for EVERY entry-point role, not just the developer (the patch wraps
    the environment, so the QA/PM roots ride the same guard; this pins it
    across the new non-developer environments)."""
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

    for role_slug in ("be-dev-1", "be-qa", "be-pm", "main-pm"):
        with _bench_environment(role_slug):
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
