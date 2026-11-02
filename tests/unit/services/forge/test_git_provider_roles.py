"""GitProvider is the composition of six role interfaces (repository, branches,
pull requests, reviews, CI, releases). A method declared by two roles would
let the MRO pick one silently, and a method declared on GitProvider itself
would sit outside every role; splitting the interface must never do either.
Its abstract surface stays exactly the union of the roles'."""

from __future__ import annotations

import inspect
from collections import defaultdict

from roboco.services.forge.base import GitProvider

# The forge operations GitService performs, all of them abstract on GitProvider.
_GIT_PROVIDER_OPERATIONS = 24


def _roles() -> list[type]:
    return [base for base in GitProvider.__bases__ if base.__name__.startswith("Forge")]


def _methods(cls: type) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not (name.startswith("__") and name.endswith("__"))
        and inspect.isfunction(value)
    }


def _abstract(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if getattr(getattr(cls, name), "__isabstractmethod__", False)
    }


def test_git_provider_composes_six_roles() -> None:
    assert sorted(role.__name__ for role in _roles()) == [
        "ForgeBranches",
        "ForgeCI",
        "ForgePullRequests",
        "ForgeReleases",
        "ForgeRepository",
        "ForgeReviews",
    ]


def test_no_two_roles_declare_the_same_method() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for role in _roles():
        for name in _methods(role):
            owners[name].append(role.__name__)
    assert {name: roles for name, roles in owners.items() if len(roles) > 1} == {}


def test_git_provider_declares_nothing_outside_its_roles() -> None:
    assert _methods(GitProvider) == set()


def test_abstract_surface_is_the_union_of_the_roles() -> None:
    union = set().union(*(_abstract(role) for role in _roles()))
    assert _abstract(GitProvider) == union
    assert len(union) == _GIT_PROVIDER_OPERATIONS
