"""Resolve the Python interpreter a target project needs.

Agents build arbitrary target projects whose Python requirement is independent
of RoboCo's own 3.13 stack. This module derives the version to provision the
agent workspace with, from the target's ``pyproject.toml`` (``requires-python``)
and ``.python-version``.

The load-bearing rule defends against uv's resolution order: uv lets a
``.python-version`` file override ``requires-python`` during interpreter
selection, so a repo pinned to 3.13 whose packages need 3.14 silently gets the
wrong interpreter. We therefore honor ``.python-version`` only when it actually
satisfies ``requires-python``; otherwise we resolve a concrete version from
``requires-python`` so the caller can pass it to uv explicitly (``--python``),
which overrides the pin.

Pure: filesystem reads only, no subprocess, no DB.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

# Candidate ``major.minor`` interpreters, lowest first. The lowest version that
# satisfies the constraint is the most portable choice (best wheel coverage).
_CANDIDATE_MINORS: tuple[str, ...] = tuple(f"3.{minor}" for minor in range(8, 20))

_PIN_RE = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True)
class ResolvedPython:
    """The interpreter to provision with, and where it came from."""

    version: str
    source: str  # "python_version_file" | "requires_python"


def satisfies(version: str, specifier: str) -> bool:
    """True iff ``version`` satisfies the PEP 440 ``specifier`` (empty = any)."""
    if not specifier.strip():
        return True
    try:
        return Version(version) in SpecifierSet(specifier)
    except (InvalidSpecifier, InvalidVersion):
        return False


def _extract_requires_python(data: dict[str, Any]) -> str | None:
    """Pull ``requires-python`` from a parsed pyproject (PEP 621, then poetry)."""
    project = data.get("project")
    if isinstance(project, dict) and project.get("requires-python"):
        return str(project["requires-python"])
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
    python = deps.get("python") if isinstance(deps, dict) else None
    if isinstance(python, str) and python.strip():
        return python
    return None


def _read_requires_python(project_root: Path) -> str | None:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    return _extract_requires_python(data)


def _read_pin(project_root: Path) -> str | None:
    """The ``major.minor`` from ``.python-version`` (tolerates ``3.13.2``,
    ``cpython-3.13``); None when absent or unparseable."""
    pin_file = project_root / ".python-version"
    if not pin_file.exists():
        return None
    try:
        match = _PIN_RE.search(pin_file.read_text())
    except OSError:
        return None
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _lowest_satisfying(specifier: str) -> str | None:
    for candidate in _CANDIDATE_MINORS:
        if satisfies(candidate, specifier):
            return candidate
    return None


def resolve_target_python(project_root: Path) -> ResolvedPython | None:
    """The interpreter to provision the agent workspace with, or None.

    None means the target declares nothing actionable (no python project / no
    constraint) — the caller should leave provisioning unchanged.
    """
    requires = _read_requires_python(project_root)
    pin = _read_pin(project_root)
    if pin is not None and (requires is None or satisfies(pin, requires)):
        return ResolvedPython(pin, "python_version_file")
    if requires is not None:
        version = _lowest_satisfying(requires)
        if version is not None:
            return ResolvedPython(version, "requires_python")
    return None
