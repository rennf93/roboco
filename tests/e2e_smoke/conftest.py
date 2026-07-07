"""e2e lifecycle smoke harness — collection gate + the stack fixture.

Scripted-agent smoke: an in-process RoboCo API (real routers, real
middleware, real gateway/choreographer/services) over the ephemeral test
Postgres, a local bare git origin standing in for GitHub, and a fake
GitHub REST layer whose merges are REAL git merges on that origin. A
deterministic driver calls the REAL MCP flow/do tool functions — no LLM
anywhere — so seam bugs (tool↔gate schema drift, squash merges, stale
refs, workspace routing) die here instead of in a live run.

Gating: excluded from the default suite (`make quality`); runs via
`make e2e-smoke` (sets ROBOCO_E2E_SMOKE=1). Needs the test Postgres
reachable and git on PATH, nothing else.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from tests.e2e_smoke.harness import build_e2e_stack

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("ROBOCO_E2E_SMOKE") == "1":
        return
    skip = pytest.mark.skip(reason="e2e smoke runs via `make e2e-smoke` only")
    for item in items:
        if "tests/e2e_smoke" in str(item.path):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_lazy_db_holder() -> Iterator[None]:
    """Drop the app's lazy ``_DbHolder`` engine/factory between e2e tests.

    Async smoke tests that drive production code using the global lazy engine
    (``get_db_context`` / ``get_session_factory``) rebind ``_DbHolder`` to
    their own function-scoped event loop. If a test leaves the holder bound to
    its (now-dead) loop, the next consumer on a different loop — the uvicorn
    server thread handling the next test's API call, or the next test itself —
    reuses a factory bound to a dead loop and asyncpg raises
    ``RuntimeError: ... Future ... attached to a different loop``. Resetting
    to ``None`` here makes every test start unbound so the first consumer
    rebinds to its own loop. Disposal is intentionally not done here: an
    engine bound to the uvicorn thread's loop cannot be awaited away from it.
    """
    yield
    from roboco.db import base as db_base

    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None


@pytest.fixture(autouse=True)
def _isolate_per_test_tables(e2e_stack: E2EStack) -> Iterator[None]:
    """Wipe the e2e DB between tests so per-test seeds never leak across tests.

    ``seed_company`` is session-cached and the canonical dev/qa/pm/... agent
    rows are shared across every test by deterministic slug. A task any test
    leaves ``pending`` assigned to that shared dev leaks into the session DB,
    and the next test's ``give_me_work`` returns the leaked task instead of its
    just-seeded one (the state-machine cascade: 8 tests got stale task "A"
    from a prior test). The e2e app has no startup seed data, so the only
    persisted state is what each test seeds. Truncating every table (agents
    included — ``agents.current_task_id`` FK-references ``tasks``, so the
    cycle means CASCADE on a partial set would wipe agents anyway) and
    invalidating ``_COMPANY_CACHE`` gives complete per-test isolation: each
    test re-seeds fresh canonical agents with new UUIDs against a clean DB.
    """
    yield

    from roboco.db.base import Base
    from sqlalchemy import text
    from tests.e2e_smoke.arcs import _COMPANY_CACHE

    names = [t.name for t in Base.metadata.sorted_tables]
    stmt = text(f"TRUNCATE TABLE {', '.join(names)} RESTART IDENTITY CASCADE")

    async def _truncate(session: AsyncSession) -> None:
        await session.execute(stmt)

    e2e_stack.run_db(_truncate)
    _COMPANY_CACHE.clear()


@pytest.fixture(scope="session")
def e2e_stack(
    _test_database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[E2EStack]:
    yield from build_e2e_stack(_test_database_url, tmp_path_factory)
