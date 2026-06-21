"""The interpreter resolver derives the target project's Python version.

uv lets a ``.python-version`` file override ``requires-python`` during
interpreter selection — that mismatch is the live failure mode (a repo pinned
to 3.13 whose packages need 3.14). The resolver defends against it: it honors
``.python-version`` only when it actually satisfies ``requires-python``,
otherwise it resolves a concrete version from ``requires-python`` so provisioning
can pass it to uv explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.services.toolchain import ResolvedPython, resolve_target_python, satisfies

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, *, pyproject: str | None = None, pin: str | None = None) -> None:
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject)
    if pin is not None:
        (root / ".python-version").write_text(pin)


def test_requires_python_only_resolves_lowest_satisfying(tmp_path: Path) -> None:
    _write(tmp_path, pyproject='[project]\nrequires-python = ">=3.14,<3.15"\n')
    assert resolve_target_python(tmp_path) == ResolvedPython("3.14", "requires_python")


def test_python_version_file_ignored_when_it_violates_requires_python(
    tmp_path: Path,
) -> None:
    # The guard-core-app bug: pin says 3.13 but the packages need 3.14.
    _write(
        tmp_path,
        pyproject='[project]\nrequires-python = ">=3.14,<3.15"\n',
        pin="3.13\n",
    )
    assert resolve_target_python(tmp_path) == ResolvedPython("3.14", "requires_python")


def test_python_version_file_honored_when_it_satisfies(tmp_path: Path) -> None:
    _write(
        tmp_path,
        pyproject='[project]\nrequires-python = ">=3.14,<3.15"\n',
        pin="3.14\n",
    )
    assert resolve_target_python(tmp_path) == ResolvedPython(
        "3.14", "python_version_file"
    )


def test_python_version_file_with_patch_and_satisfying(tmp_path: Path) -> None:
    _write(
        tmp_path,
        pyproject='[project]\nrequires-python = ">=3.12"\n',
        pin="3.13.2\n",
    )
    assert resolve_target_python(tmp_path) == ResolvedPython(
        "3.13", "python_version_file"
    )


def test_open_lower_bound_picks_that_minor(tmp_path: Path) -> None:
    _write(tmp_path, pyproject='[project]\nrequires-python = ">=3.12,<3.13"\n')
    assert resolve_target_python(tmp_path) == ResolvedPython("3.12", "requires_python")


def test_poetry_table_requires_python(tmp_path: Path) -> None:
    _write(
        tmp_path,
        pyproject='[tool.poetry.dependencies]\npython = ">=3.14,<3.15"\n',
    )
    assert resolve_target_python(tmp_path) == ResolvedPython("3.14", "requires_python")


def test_no_python_project_returns_none(tmp_path: Path) -> None:
    # No pyproject and no pin → nothing to resolve; caller leaves uv unchanged.
    assert resolve_target_python(tmp_path) is None


def test_pyproject_without_requires_python_and_no_pin_returns_none(
    tmp_path: Path,
) -> None:
    _write(tmp_path, pyproject='[project]\nname = "x"\nversion = "0"\n')
    assert resolve_target_python(tmp_path) is None


def test_pin_only_no_requires_python_is_honored(tmp_path: Path) -> None:
    # A pin with no requires-python constraint is trivially satisfying.
    _write(tmp_path, pyproject='[project]\nname = "x"\nversion = "0"\n', pin="3.13\n")
    assert resolve_target_python(tmp_path) == ResolvedPython(
        "3.13", "python_version_file"
    )


def test_satisfies_helper() -> None:
    assert satisfies("3.14", ">=3.14,<3.15") is True
    assert satisfies("3.13", ">=3.14,<3.15") is False
    assert satisfies("3.12", "") is True
