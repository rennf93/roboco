"""ReviewerService is composed from mixins. A method defined in two of them,
or redefined on ReviewerService itself, would let the MRO pick one silently;
splitting the class must never do that. The mixins' shared type,
ReviewerServiceSelf, declares each class's cross-class members on that class's
surface, which must follow the class."""

from __future__ import annotations

import inspect
from collections import defaultdict

from roboco.services.base import BaseService
from roboco.services.optimal_brain._reviewer_service_types import ReviewerServiceSelf
from roboco.services.optimal_brain.reviewer import ReviewerService


def _mixins() -> list[type]:
    return [
        base
        for base in ReviewerService.__mro__[1:]
        if base not in {BaseService, object}
    ]


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


def test_no_two_mixins_define_the_same_member() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for mixin in _mixins():
        for name in _members(mixin):
            owners[name].append(mixin.__name__)
    shared = {name: names for name, names in owners.items() if len(names) > 1}
    assert shared == {}


def test_reviewer_service_redefines_no_mixin_member() -> None:
    mixin_members = set().union(*(_members(mixin) for mixin in _mixins()))
    assert _members(ReviewerService) & mixin_members == set()


def test_each_surface_declares_only_members_its_class_defines() -> None:
    classes = {cls.__name__: cls for cls in (ReviewerService, *_mixins())}
    strays: dict[str, set[str]] = {}
    for surface in ReviewerServiceSelf.__bases__:
        if not surface.__name__.endswith("Surface"):
            continue
        stem = surface.__name__.removesuffix("Surface")
        owner = classes.get("ReviewerService" if stem == "Reviewer" else stem)
        declared = set(inspect.get_annotations(surface)) | {
            name
            for name, value in vars(surface).items()
            if not name.startswith("__")
            and (
                inspect.isfunction(value)
                or isinstance(value, staticmethod | classmethod | property)
            )
        }
        undefined = declared - (set(vars(owner)) if owner else set())
        if undefined:
            strays[surface.__name__] = undefined
    assert strays == {}
