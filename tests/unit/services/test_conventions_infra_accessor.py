"""ConventionsService.effective_infra_globs: the Stage 3 gate's read API.

Contract: the accessor resolves the effective map exactly like ``get_map``
(reuse, no duplicated resolution) and returns the merged globs. It must work
REGARDLESS of ``ROBOCO_CONVENTIONS_ENABLED``: the flag gates author-facing
enforcement, not the declaration read (precedent: ``GET
/api/projects/{id}/conventions`` calls ``get_map`` unflagged). The accessor
performs no flag check at all, which is what makes that contract hold; the
flag-independence tests below assert the flag being off changes nothing.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.conventions import (
    DEFAULT_INFRA_GLOBS,
    ConventionsStandard,
)
from roboco.services.conventions import ConventionsService


def _service_with_map(mapping: ConventionsStandard) -> ConventionsService:
    svc = ConventionsService(session=cast("Any", None))

    async def _fake_get_map(_project: Any, **_kwargs: Any) -> ConventionsStandard:
        return mapping

    svc.get_map = _fake_get_map  # type: ignore[assignment, method-assign]
    return svc


async def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the conventions flag at off (restored after each test)."""
    monkeypatch.setattr(settings, "conventions_enabled", False)


@pytest.mark.asyncio
async def test_accessor_returns_declared_globs(monkeypatch: pytest.MonkeyPatch) -> None:
    await _flag_off(monkeypatch)
    svc = _service_with_map(ConventionsStandard(infra=["gitops/**"]))

    assert await svc.effective_infra_globs(cast("Any", uuid4())) == ["gitops/**"]


@pytest.mark.asyncio
async def test_accessor_returns_defaults_when_undeclared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _flag_off(monkeypatch)
    svc = _service_with_map(ConventionsStandard(infra=None))

    globs = await svc.effective_infra_globs(cast("Any", uuid4()))
    assert globs == DEFAULT_INFRA_GLOBS
    # A fresh list, not the module constant aliased (callers may mutate).
    assert globs is not DEFAULT_INFRA_GLOBS


@pytest.mark.asyncio
async def test_accessor_preserves_explicit_optout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _flag_off(monkeypatch)
    svc = _service_with_map(ConventionsStandard(infra=[]))

    # [] is a real opt-out, not a missing declaration: it must NOT be
    # expanded back to the defaults.
    assert await svc.effective_infra_globs(cast("Any", uuid4())) == []
