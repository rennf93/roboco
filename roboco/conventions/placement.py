"""Placement checks: flag a definition whose kind is forbidden in its module.

A file is mapped to the most specific module whose ``path`` is a directory
prefix of it; any classified definition whose kind is in that module's
``forbidden`` list becomes a ``Finding``. A test or docs file maps to no module:
it is never placement-enforced, wherever it sits. The rule name follows the org
convention ``no_<kind>s_in_<module-leaf>`` so it lines up with ``BUILTIN_RULES``
(e.g. a model in ``app/routers`` → ``no_models_in_routers``).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from roboco.foundation.policy.conventions.models import (
    ConventionsStandard,
    DefinitionKind,
    Module,
)

from .findings import Finding

Definition = tuple[str, int, DefinitionKind]

# Directory trees that are never placement-enforced. Test trees legitimately
# define fixtures, fakes, and helper functions of every kind, and documentation
# trees (e.g. a docs site) are not code at all. The scan drops a candidate
# module under any of these segments, and a file under one maps to no module,
# so a test colocated inside a source module (``src/hooks/__tests__/``) is
# treated exactly like one under a top-level ``tests/``. This holds for any
# project, not just RoboCo.
NON_SOURCE_SEGMENTS = frozenset({"tests", "test", "docs", "__tests__"})

# Jest's and Vitest's default test-file match: a ``*.test.*`` / ``*.spec.*``
# file is a test even when it sits beside the code it tests, outside any
# test tree.
_TEST_FILE_INFIXES = (".test.", ".spec.")


def check_placement(
    rel_path: str, defs: list[Definition], standard: ConventionsStandard
) -> list[Finding]:
    """Return placement findings for ``defs`` in the file at ``rel_path``."""
    module = _module_for(rel_path, standard)
    if module is None or not module.forbidden:
        return []
    return [
        _finding(rel_path, d, module, standard)
        for d in defs
        if d[2] in module.forbidden
    ]


def is_non_source_path(rel_path: str) -> bool:
    """True for a file under a test / docs tree or named like a test file."""
    path = PurePosixPath(rel_path)
    if NON_SOURCE_SEGMENTS.intersection(path.parent.parts):
        return True
    return any(infix in path.name for infix in _TEST_FILE_INFIXES)


def _module_for(rel_path: str, standard: ConventionsStandard) -> Module | None:
    if is_non_source_path(rel_path):
        return None
    matches = [m for m in standard.modules if _path_in_module(rel_path, m.path)]
    return max(matches, key=lambda m: len(m.path)) if matches else None


def _path_in_module(rel_path: str, module_path: str) -> bool:
    stem = module_path.rstrip("/")
    return rel_path == stem or rel_path.startswith(stem + "/")


def _finding(
    rel_path: str, definition: Definition, module: Module, standard: ConventionsStandard
) -> Finding:
    name, line, kind = definition
    rule = f"no_{kind}s_in_{_module_leaf(module.path)}"
    level = standard.rules[rule].level if rule in standard.rules else "block"
    return Finding(
        file=rel_path,
        line=line,
        kind=kind,
        rule=rule,
        level=level,
        message=(
            f"{kind} '{name}' defined in {module.path} — "
            f"{kind}s are forbidden here ({module.purpose})"
        ),
        fix_hint=(
            f"move '{name}' out of {module.path} into the module that owns {kind}s"
        ),
    )


def _module_leaf(module_path: str) -> str:
    return module_path.rstrip("/").rsplit("/", 1)[-1]
