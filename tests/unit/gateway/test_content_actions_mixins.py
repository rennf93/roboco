"""ContentActions is composed from mixins. A method defined in two of them,
or redefined on ContentActions itself, would let the MRO pick one silently;
splitting the class must never do that. The mixins' shared type,
ContentActionsSelf, declares each class's cross-mixin members on that
class's surface, which must follow the class."""

from __future__ import annotations

import inspect
from collections import defaultdict

from roboco.services.gateway._content_actions_types import ContentActionsSelf
from roboco.services.gateway.content_actions import ContentActions


def _mixins() -> list[type]:
    return [base for base in ContentActions.__mro__[1:] if base is not object]


def _methods(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if inspect.isfunction(value)
        or isinstance(value, staticmethod | classmethod | property)
    }


def test_no_two_mixins_define_the_same_method() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for mixin in _mixins():
        for name in _methods(mixin):
            owners[name].append(mixin.__name__)
    shared = {name: names for name, names in owners.items() if len(names) > 1}
    assert shared == {}


def test_content_actions_redefines_no_mixin_method() -> None:
    mixin_methods = set().union(*(_methods(mixin) for mixin in _mixins()))
    assert _methods(ContentActions) & mixin_methods == set()


def test_each_surface_declares_only_members_its_class_defines() -> None:
    classes = {cls.__name__: cls for cls in (ContentActions, *_mixins())}
    strays: dict[str, set[str]] = {}
    for surface in ContentActionsSelf.__bases__:
        if not surface.__name__.endswith("Surface"):
            continue
        stem = surface.__name__.removesuffix("Surface")
        owner = classes.get("ContentActions" if stem == "Content" else stem + "Actions")
        declared = {name for name in _methods(surface) if not name.startswith("__")}
        undefined = declared - (set(vars(owner)) if owner else set())
        if undefined:
            strays[surface.__name__] = undefined
    assert strays == {}
