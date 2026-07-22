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

Gating: like every other module here, this is skipped unless
``ROBOCO_E2E_SMOKE=1`` (see ``tests/e2e_smoke/conftest.py``'s
``pytest_collection_modifyitems``) — it needs the real test Postgres, which
``EvalRunner`` provisions its own throwaway copy of (see
``roboco/eval/runner.py``'s ``_scratch_database``), independent of this
package's shared session-scoped ``e2e_stack`` fixture.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

from roboco.config import settings
from roboco.eval.fixtures import FIXTURES
from roboco.eval.runner import BenchJudge, EvalRunner, JudgeVerdict, _bench_environment
from tests.e2e_smoke.arcs import Company, dev_arc, doc_arc, qa_arc
from tests.e2e_smoke.harness import ScriptedAgent

if TYPE_CHECKING:
    import pytest
    from roboco.eval.fixtures import BenchTaskSpec
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
    """

    def __init__(self, stack: E2EStack) -> None:
        self._stack = stack
        self._company = _stub_company()

    async def run_stage(self, *, task: dict[str, Any], agent_slug: str) -> None:
        await asyncio.to_thread(self._run_stage_sync, task, agent_slug)

    def _run_stage_sync(self, task: dict[str, Any], agent_slug: str) -> None:
        from roboco.agents_config import get_agent_role

        role = get_agent_role(agent_slug)
        task_id = UUID(task["id"])
        if role == "developer":
            dev_arc(
                self._stack,
                self._company,
                task["project_slug"],
                task_id,
                work=(f"bench/{_FIXTURE_KEY}/paginate.py", _FIXED_FIX),
            )
        elif role == "qa":
            qa_arc(self._stack, self._company, task_id)
        elif role == "documenter":
            doc_arc(
                self._stack,
                self._company,
                task_id,
                filename=f"bench/{_FIXTURE_KEY}/paginate.py",
            )
        elif role == "cell_pm":
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
