"""#162: ``_register_tools`` raises on a missing manifest — dev/test override.

In production the manifest is the role-authoritative tool list and a missing
one must fail loud (an agent without its manifest would see off-role verbs).
But dev/test imports of the server modules need an escape hatch that does not
require hand-writing a manifest file. ``ROBOCO_ALLOW_FULL_TOOLSET`` lets a
missing manifest fall back to registering the full tool set instead of
raising — default-off so production behaviour is unchanged.

Registration runs when the module is imported, so each test imports the
server module anew (``fresh_import``), whatever this process imported
before, and puts back exactly what was there afterwards. The tests used to
import the module and then reload it, which only raised inside
``pytest.raises`` when an earlier test had already imported it: run alone, or
first on a worker, the import itself raised.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from types import ModuleType

_MISSING = object()


@pytest.fixture
def fresh_import() -> Iterator[Callable[[str], ModuleType]]:
    """Import a module anew; afterwards restore its ``sys.modules`` entry and
    its parent package's attribute to what they were (absent included)."""
    saved: list[tuple[str, object, ModuleType, str, object]] = []

    def load(name: str) -> ModuleType:
        package, _, attr = name.rpartition(".")
        parent = importlib.import_module(package)
        before = sys.modules.pop(name, _MISSING)
        saved.append((name, before, parent, attr, getattr(parent, attr, _MISSING)))
        return importlib.import_module(name)

    yield load
    for name, module, parent, attr, attribute in reversed(saved):
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = cast("ModuleType", module)
        if attribute is _MISSING:
            if hasattr(parent, attr):
                delattr(parent, attr)
        else:
            setattr(parent, attr, attribute)


def _seed_no_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    # Point at a path that does not exist — no manifest.
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(missing))
    monkeypatch.delenv("ROBOCO_ALLOW_FULL_TOOLSET", raising=False)
    return missing


@pytest.mark.parametrize("server", ["flow_server", "do_server"])
def test_missing_manifest_raises_without_override(
    server: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fresh_import: Callable[[str], ModuleType],
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="manifest unavailable"):
        fresh_import(f"roboco.mcp.{server}")


@pytest.mark.parametrize("server", ["flow_server", "do_server"])
def test_missing_manifest_falls_back_with_override(
    server: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fresh_import: Callable[[str], ModuleType],
) -> None:
    _seed_no_manifest(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    srv = fresh_import(f"roboco.mcp.{server}")
    # Full tool set registered — every tool is on the palette.
    assert set(srv._REGISTERED_TOOLS) == set(srv._TOOLS.keys())
