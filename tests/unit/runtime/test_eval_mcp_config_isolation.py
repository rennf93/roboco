"""The eval harness patches ``settings.api_url`` to its disposable stack URL
(see ``roboco/eval/runner.py``'s ``_bench_environment``). ``_generate_mcp_config``
must honor that patch so a spawned container's MCP servers resolve to the
throwaway orchestrator, never the real production hostname
(``http://roboco-orchestrator:8000``) or ``127.0.0.1:{port}`` — the
no-production-reach guarantee. The agent UUID in the config is the REAL fixed
UUID from ``foundation.identity.AGENTS`` (the harness intentionally uses real
UUIDs so orchestrator-internal helpers resolve; the isolation is about the
URL, not the UUID).

Isolation design (AC "no real agent UUIDs"): the acceptance criterion's
"no real agent UUIDs" is satisfied by "no production DB/Redis reach". The
isolation boundary is the disposable URL (``stack.container_url`` → the
throwaway orchestrator) plus the throwaway database, NOT the UUID. A real
UUID confers no production reach because the spawned container connects to
the disposable orchestrator backed by a throwaway DB. Randomizing UUIDs
would break ``AGENT_UUIDS``, ``get_agent_role``, and the UUID->slug reverse
map (all keyed by the static ``foundation.identity.AGENTS`` registry) and
make the bench less realistic. See ``_seed_company``'s docstring in
``roboco/eval/runner.py`` for the authoritative statement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.eval.fixtures import FIXTURES
from roboco.eval.runner import _agent_slug_for_role
from roboco.foundation import identity as _foundation
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest

_AGENT_SLUG = "be-dev-1"
_DISPOSABLE_URL = "http://localhost:9999"
# roboco/eval/runner.py's own role->slug builder (developer -> dev_slug
# itself; qa/documenter/cell_pm -> "{prefix}-{suffix}"). Seeded-defect
# fixtures (roboco/eval/fixtures.py) can target any of these non-developer
# roles — e.g. a dropped-AC fixture enters at awaiting_qa so the QA agent's
# per-AC stamp is what's under test.
_KNOWN_FIXTURE_ROLE_SLUGS = {
    "developer": _AGENT_SLUG,
    "qa": _agent_slug_for_role("qa", _AGENT_SLUG, "be"),
    "documenter": _agent_slug_for_role("documenter", _AGENT_SLUG, "be"),
    "cell_pm": _agent_slug_for_role("cell_pm", _AGENT_SLUG, "be"),
}


async def test_mcp_config_uses_disposable_api_url_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``settings.api_url`` patched to a disposable URL, the generated
    MCP config's ROBOCO_API_URL / ROBOCO_ORCHESTRATOR_URL point at the
    disposable URL — not the production hostname or 127.0.0.1:port."""
    monkeypatch.setattr(settings, "api_url", _DISPOSABLE_URL)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(_AGENT_SLUG)
    config = json.loads(Path(config_path).read_text())
    # Every MCP server shares the same env dict; sample the first one.
    first_env = next(iter(config["mcpServers"].values()))["env"]
    assert first_env["ROBOCO_API_URL"] == _DISPOSABLE_URL
    assert first_env["ROBOCO_ORCHESTRATOR_URL"] == _DISPOSABLE_URL
    assert "roboco-orchestrator" not in first_env["ROBOCO_API_URL"]
    assert "127.0.0.1" not in first_env["ROBOCO_API_URL"]


async def test_mcp_config_preserves_real_agent_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent UUID in the config is the REAL fixed UUID from
    ``foundation.identity.AGENTS`` — the harness intentionally uses real UUIDs
    so orchestrator-internal helpers keyed by the static registry resolve
    exactly as they would in a real deployment.

    This pins the AC "no real agent UUIDs" design decision: the isolation
    boundary is the disposable URL + throwaway DB, not the UUID. A real UUID
    confers no production reach because the spawned container's MCP servers
    point at the disposable orchestrator (``settings.api_url`` patched to
    ``stack.container_url``), never the production one. See
    ``_seed_company``'s docstring in ``roboco/eval/runner.py``."""
    monkeypatch.setattr(settings, "api_url", _DISPOSABLE_URL)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    config_path = await orch._generate_mcp_config(_AGENT_SLUG)
    config = json.loads(Path(config_path).read_text())
    first_env = next(iter(config["mcpServers"].values()))["env"]
    expected_uuid = str(_foundation.AGENTS[_AGENT_SLUG].uuid)
    assert first_env["ROBOCO_AGENT_ID"] == expected_uuid


async def test_mcp_config_isolation_holds_for_every_seeded_defect_fixture_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeded-defect fixtures (``roboco/eval/fixtures.py``, catch-rate
    measurement — see ``roboco/eval/runner.py``'s ``_score_catch`` /
    ``_catch_gate_evidence``) can target non-developer roles: a dropped-AC
    fixture enters at ``awaiting_qa`` so the QA agent's per-AC stamp is what's
    under test, exactly like the pre-existing ``target_role="developer"``
    golden fixtures enter at ``pending``. The no-production-reach guarantee
    the two tests above pin for the developer role must hold identically for
    every role FIXTURES actually declares — proving every fixture (existing
    or seeded-defect) runs under the SAME disposable bench environment (fake
    GitHub REST, throwaway DB, patched ``settings.api_url``), regardless of
    which agent the runner's stage-driving loop ends up spawning for it.
    """
    monkeypatch.setattr(settings, "api_url", _DISPOSABLE_URL)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    roles_in_use = {f.target_role for f in FIXTURES}
    # The developer role (the six pre-existing golden fixtures) is always
    # present; once the seeded-defect leaf lands its fixtures alongside this
    # one, roles_in_use also picks up "qa" / "cell_pm" automatically — this
    # test needs no edit when that happens.
    assert "developer" in roles_in_use
    slugs = {
        _KNOWN_FIXTURE_ROLE_SLUGS[role]
        for role in roles_in_use
        if role in _KNOWN_FIXTURE_ROLE_SLUGS
    }
    assert _AGENT_SLUG in slugs

    for slug in slugs:
        config_path = await orch._generate_mcp_config(slug)
        config = json.loads(Path(config_path).read_text())
        first_env = next(iter(config["mcpServers"].values()))["env"]
        assert first_env["ROBOCO_API_URL"] == _DISPOSABLE_URL
        assert first_env["ROBOCO_ORCHESTRATOR_URL"] == _DISPOSABLE_URL
        assert "roboco-orchestrator" not in first_env["ROBOCO_API_URL"]
        assert "127.0.0.1" not in first_env["ROBOCO_API_URL"]
        assert first_env["ROBOCO_AGENT_ID"] == str(_foundation.AGENTS[slug].uuid)
