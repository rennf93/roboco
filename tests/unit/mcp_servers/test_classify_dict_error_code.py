"""Dict ``error.code`` → breaker-kind classification (#161).

The exception handlers return ``error`` as a DICT (``{code, message, details?}``)
rather than a string kind, so ``_classify_rejection`` maps the code to a counted
breaker kind via ``_classify_dict_error_code``. The old classifier was
substring-only: ``AUTHENTICATION_REQUIRED`` carries no ``AUTHORIZED`` /
``DENIED`` / ``PERMISSION`` substring, so it dropped to ``invalid_state``
instead of ``not_authorized`` — an auth storm was attributed as a state storm.
The fix is an exact-code map (authoritative for the codes the handlers emit)
with a substring fallback for forward-compat with new codes.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import types
    from pathlib import Path


_MINIMAL_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000042",
    "role": "developer",
    "team": "backend",
    "workspace_path": "/tmp/test",
    "flow_tools": ["give_me_work", "i_am_done"],
    "do_tools": ["commit", "note"],
    "read_tools": [],
    "write_tools": [],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


def _seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_MINIMAL_MANIFEST))
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000042")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))
    return manifest_path


@pytest.fixture
def flow_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    _seed(monkeypatch, tmp_path)
    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


@pytest.fixture
def do_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    _seed(monkeypatch, tmp_path)
    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    return srv


# Real codes the exception handlers emit — the exact map must classify each
# correctly, where the old substring rule misclassified AUTHENTICATION_REQUIRED.
@pytest.mark.parametrize(
    "code,expected",
    [
        ("AUTHENTICATION_REQUIRED", "not_authorized"),
        ("CHANNEL_ACCESS_DENIED", "not_authorized"),
        ("JOURNAL_ACCESS_DENIED", "not_authorized"),
        ("PERMISSION_DENIED", "not_authorized"),
        ("INVALID_INPUT", "incomplete_input"),
        ("VALIDATION_ERROR", "incomplete_input"),
        ("INVALID_STATE", "invalid_state"),
        ("TASK_LIFECYCLE_ERROR", "invalid_state"),
        ("TASK_OWNERSHIP_ERROR", "invalid_state"),
        ("SERVICE_ERROR", "invalid_state"),
        ("NOT_FOUND", None),
    ],
)
def test_flow_classifies_known_codes(
    flow_module: types.ModuleType, code: str, expected: str | None
) -> None:
    assert flow_module._classify_dict_error_code(code) == expected


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AUTHENTICATION_REQUIRED", "not_authorized"),
        ("PERMISSION_DENIED", "not_authorized"),
        ("VALIDATION_ERROR", "incomplete_input"),
        ("INVALID_STATE", "invalid_state"),
        ("NOT_FOUND", None),
    ],
)
def test_do_classifies_known_codes(
    do_module: types.ModuleType, code: str, expected: str | None
) -> None:
    assert do_module._classify_dict_error_code(code) == expected


def test_flow_authentication_required_is_not_authorized(
    flow_module: types.ModuleType,
) -> None:
    """The regression: AUTHENTICATION_REQUIRED has no AUTHORIZED/DENIED substring."""
    assert flow_module._classify_dict_error_code("AUTHENTICATION_REQUIRED") == (
        "not_authorized"
    )


def test_flow_unknown_code_falls_back_to_substring(
    flow_module: types.ModuleType,
) -> None:
    """A new RobocoError code still classifies via the substring fallback."""
    assert (
        flow_module._classify_dict_error_code("A2A_ACCESS_DENIED") == "not_authorized"
    )
    assert flow_module._classify_dict_error_code("SOMETHING_BROKE") == "invalid_state"


def test_flow_not_found_substring_still_uncounted(
    flow_module: types.ModuleType,
) -> None:
    """An unknown code carrying a NOT_FOUND substring stays uncounted (None)."""
    assert flow_module._classify_dict_error_code("NOTE_NOT_FOUND") is None


def test_flow_classify_rejection_routes_dict_error(
    flow_module: types.ModuleType,
) -> None:
    """_classify_rejection hands a dict error to the code classifier."""
    payload = {"error": {"code": "AUTHENTICATION_REQUIRED", "message": "no"}}
    assert flow_module._classify_rejection(payload) == "not_authorized"
