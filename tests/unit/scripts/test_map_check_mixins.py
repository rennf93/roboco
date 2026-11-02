"""scripts/verify_codebase_map.py's MapCheck is composed from mixins. A method
defined in two of them, or redefined on MapCheck itself, would let the MRO pick
one silently; splitting the class must never do that. The mixins' shared type,
MapCheckSelf, lives in the script under TYPE_CHECKING and declares each class's
cross-class members on that class's surface, which must follow the class."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).parents[3] / "scripts" / "verify_codebase_map.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_codebase_map", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vcm = _load_module()


def _mixins() -> list[type]:
    return [base for base in vcm.MapCheck.__mro__[1:] if base is not object]


def _members(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not (name.startswith("__") and name.endswith("__"))
        and (
            inspect.isfunction(value)
            or isinstance(value, staticmethod | classmethod | property)
            or not callable(value)
        )
    }


def _surfaces() -> dict[str, set[str]]:
    """The Protocol surfaces the script declares under TYPE_CHECKING, by name,
    with the members each declares (read from the source: they never exist at
    runtime)."""
    tree = ast.parse(SCRIPT.read_text())
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for cls in node.body:
                if isinstance(cls, ast.ClassDef) and cls.name.endswith("Surface"):
                    out[cls.name] = {
                        m.name
                        if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                        else ast.unparse(m.target)
                        for m in cls.body
                        if isinstance(
                            m, ast.FunctionDef | ast.AsyncFunctionDef | ast.AnnAssign
                        )
                    }
    return out


def test_no_two_mixins_define_the_same_member() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for mixin in _mixins():
        for name in _members(mixin):
            owners[name].append(mixin.__name__)
    shared = {name: names for name, names in owners.items() if len(names) > 1}
    assert shared == {}


def test_map_check_redefines_no_mixin_member() -> None:
    mixin_members = set().union(*(_members(mixin) for mixin in _mixins()))
    assert _members(vcm.MapCheck) & mixin_members == set()


def test_each_surface_declares_only_members_its_class_defines() -> None:
    surfaces = _surfaces()
    assert surfaces, "no Surface protocols found under TYPE_CHECKING"
    classes = {cls.__name__: cls for cls in (vcm.MapCheck, *_mixins())}
    strays: dict[str, set[str]] = {}
    for surface, declared in surfaces.items():
        owner = classes.get(surface.removesuffix("Surface"))
        # Dataclass fields without a default live only in the annotations.
        defined = (
            set(vars(owner)) | set(inspect.get_annotations(owner)) if owner else set()
        )
        undefined = declared - defined
        if undefined:
            strays[surface] = undefined
    assert strays == {}
