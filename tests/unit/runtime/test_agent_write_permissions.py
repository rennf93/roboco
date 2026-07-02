"""#167: agents could never Edit/Write any file (smoke-10..14).

Root cause: _generate_agent_settings put ``Write(*)``/``Edit(*)`` in the
GLOBAL base_deny. Claude Code evaluates rules deny -> ask -> allow and the
first match wins, so a deny ALWAYS beats a more-specific allow (the glob
syntax has no negation). The global deny therefore unconditionally
shadowed every per-role workspace-scoped Write/Edit allow — every agent,
developers included, got "Edit exists but is not enabled in this context"
and fell back to destructive bash redirection (clobbering real files;
e.g. a 207-line README rewritten to a 3-line stub, which QA correctly
failed).

Second defect: the workspace allow used a SINGLE leading slash
(``Write(/data/...)``). Claude Code resolves a single ``/`` against the
settings.json project root, not the container filesystem root, so even
without the global deny the allow never matched. Absolute container
paths require the ``//`` form.

Fix: drop Write(*)/Edit(*) from base_deny (roles that must not write keep
their OWN Write(*)/Edit(*) deny); emit the workspace allow in the ``//``
absolute form.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


_WS = "/data/workspaces/roboco-api/backend/be-dev-1"
_CELL = "/data/workspaces/roboco-api/backend"

_WRITER_ROLES = ("developer", "documenter", "product_owner", "head_marketing")
_NON_WRITER_ROLES = ("qa", "cell_pm", "main_pm", "auditor")


def test_generated_settings_cap_bash_output() -> None:
    """Agent settings carry an explicit Bash-output cap — a gate/test dump
    enters context once and is re-read at cache-read price every later turn."""
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-dev-1",
        role="developer",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
    )
    settings = json.loads(Path(path).read_text())
    assert settings["env"]["BASH_MAX_OUTPUT_LENGTH"] == "20000"


def test_generated_settings_base_deny_has_no_global_write_edit() -> None:
    """The settings file a developer is spawned with must NOT globally
    deny Write/Edit (that shadowed the workspace allow → unusable)."""
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-dev-1",
        role="developer",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
    )
    settings = json.loads(Path(path).read_text())
    deny = settings["permissions"]["deny"]
    allow = settings["permissions"]["allow"]

    assert "Write(*)" not in deny, deny
    assert "Edit(*)" not in deny, deny
    # The security denies that DO rely on deny-always-wins must remain.
    assert "Bash(git:*)" in deny, deny
    assert any(".git/config" in d for d in deny), deny
    # Workspace allow present and in the // absolute form.
    assert f"Write(/{_WS}/**)" in allow, allow
    assert f"Edit(/{_WS}/**)" in allow, allow


def test_writer_roles_use_double_slash_absolute_allow() -> None:
    """Every role that authors files emits Write/Edit allow rules in the
    // absolute-filesystem form (single / silently never matches)."""
    orch = _orch()
    for role in _WRITER_ROLES:
        perms = orch._get_role_permissions(
            role=role, workspace_path=_WS, cell_workspace_path=_CELL
        )
        write_edit = [e for e in perms["allow"] if e.startswith(("Write(", "Edit("))]
        assert write_edit, f"{role} should allow some Write/Edit: {perms}"
        for entry in write_edit:
            inner = entry[entry.index("(") + 1 :]
            assert inner.startswith("//"), (
                f"{role} allow rule must use // absolute form: {entry}"
            )


def test_non_writer_roles_still_deny_write_edit() -> None:
    """Removing the GLOBAL deny must not let QA / PMs / auditor write —
    they carry their own Write(*)/Edit(*) deny in the role config."""
    orch = _orch()
    for role in _NON_WRITER_ROLES:
        perms = orch._get_role_permissions(
            role=role, workspace_path=_WS, cell_workspace_path=_CELL
        )
        assert "Write(*)" in perms["deny"], f"{role}: {perms}"
        assert "Edit(*)" in perms["deny"], f"{role}: {perms}"


def test_non_writer_generated_settings_block_write() -> None:
    """End-to-end: a cell_pm's generated settings still deny Write/Edit
    (their own role deny survives the base_deny change)."""
    orch = _orch()
    path = orch._generate_agent_settings(
        agent_id="be-pm",
        role="cell_pm",
        workspace_path=_WS,
        cell_workspace_path=_CELL,
    )
    deny = json.loads(Path(path).read_text())["permissions"]["deny"]
    assert "Write(*)" in deny, deny
    assert "Edit(*)" in deny, deny
