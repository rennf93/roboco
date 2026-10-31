"""ChoreographerHelpers: the typed surfaces the choreographer's classes inherit.

Under TYPE_CHECKING every mixin and ``BaseChoreographer`` inherit
``ChoreographerHelpers``, which composes one surface per class: ``FooSurface``
declares the methods ``FooMixin`` (``BaseChoreographer`` for
``BaseChoreographerSurface``) offers the others. The real methods live on those
classes and resolve via MRO; a stub raises NotImplementedError when called
directly. One test keeps every surface naming only methods its class defines,
so a method that moves takes its stub along; the other calls every stub, so
each raises and the file shows up in coverage.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers


def _surfaces() -> list[type]:
    return [
        base
        for base in ChoreographerHelpers.__bases__
        if base.__name__.endswith("Surface")
    ]


def _stub_names(surface: type) -> list[str]:
    return [name for name in vars(surface) if not name.startswith("__")]


def _placeholder_arguments(member: Any) -> tuple[list[None], dict[str, None]]:
    """``None`` for every required parameter: a stub raises before reading any."""
    args: list[None] = []
    kwargs: dict[str, None] = {}
    for param in inspect.signature(member).parameters.values():
        if param.default is not inspect.Parameter.empty:
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = None
        elif param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            args.append(None)
    return args, kwargs


def test_each_surface_declares_only_methods_its_class_defines() -> None:
    classes = {cls.__name__: cls for cls in Choreographer.__mro__[1:]}
    strays: dict[str, set[str]] = {}
    for surface in _surfaces():
        stem = surface.__name__.removesuffix("Surface")
        owner = classes.get(f"{stem}Mixin") or classes.get(stem)
        defined = set(vars(owner)) if owner is not None else set()
        undefined = set(_stub_names(surface)) - defined
        if undefined:
            strays[surface.__name__] = undefined
    assert strays == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "name"),
    [(surface, name) for surface in _surfaces() for name in _stub_names(surface)],
    ids=lambda value: value.__name__ if isinstance(value, type) else value,
)
async def test_every_stub_raises(surface: type, name: str) -> None:
    with pytest.raises(NotImplementedError):
        member = getattr(surface(), name)
        args, kwargs = _placeholder_arguments(member)
        result = member(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
