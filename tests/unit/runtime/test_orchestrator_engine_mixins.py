"""AgentOrchestrator is composed from engine mixins. A method defined in two
of them, or redefined on the orchestrator itself, would let the MRO pick one
silently; splitting an engine into smaller ones must never do that. The
mixins' shared type, AgentOrchestratorSelf, declares each engine's
cross-mixin methods on that engine's surface, which must follow the engine."""

from __future__ import annotations

import inspect
from collections import defaultdict

from roboco.runtime.engines._types import AgentOrchestratorSelf
from roboco.runtime.orchestrator import AgentOrchestrator


def _engine_bases() -> list[type]:
    return [
        base
        for base in AgentOrchestrator.__mro__[1:]
        if base.__module__.startswith("roboco.runtime.engines")
    ]


def _methods(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if inspect.isfunction(value)
        or isinstance(value, staticmethod | classmethod | property)
    }


def test_no_two_engines_define_the_same_method() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for base in _engine_bases():
        for name in _methods(base):
            owners[name].append(base.__name__)
    shared = {name: names for name, names in owners.items() if len(names) > 1}
    assert shared == {}


def test_the_orchestrator_redefines_no_engine_method() -> None:
    engine_methods = set().union(*(_methods(base) for base in _engine_bases()))
    assert _methods(AgentOrchestrator) & engine_methods == set()


def test_each_surface_declares_only_methods_its_engine_defines() -> None:
    engines = {base.__name__: base for base in _engine_bases()}
    strays: dict[str, set[str]] = {}
    for surface in AgentOrchestratorSelf.__bases__:
        if not surface.__name__.endswith("Surface"):
            continue
        engine = engines.get(surface.__name__.removesuffix("Surface") + "Engine")
        declared = {name for name in _methods(surface) if not name.startswith("__")}
        undefined = declared - (_methods(engine) if engine else set())
        if undefined:
            strays[surface.__name__] = undefined
    assert strays == {}
