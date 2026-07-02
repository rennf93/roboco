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


@pytest.fixture(scope="session")
def e2e_stack(
    _test_database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[E2EStack]:
    yield from build_e2e_stack(_test_database_url, tmp_path_factory)
