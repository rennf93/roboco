"""ConventionsService cache schema-stamp: pre-stamp rows are stale.

The effective-map cache (migration 043) is keyed on ``(project_id,
commit_sha)`` with no version, so rows written before a payload-shape change
(e.g. the ``infra`` declaration) would keep serving maps without the new
field until HEAD moves. The fix stamps the JSONB payload with
``_CACHE_SCHEMA_VERSION``: pre-stamp rows read as stale, get dropped, and
the next ``_cache_put`` repopulates them.

These tests exercise that with a fake session, following
``test_conventions_cache_put.py``: the stale-row purge must run inside a
savepoint (best-effort write on a shared session, never poisons it), and
``_cache_put`` must embed the stamp in the stored payload.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.foundation.policy.conventions import ConventionsStandard
from roboco.services.conventions import (
    _CACHE_SCHEMA_VERSION,
    ConventionsService,
    _cache_row_current,
)


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeNested:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        self._session.savepoint_started += 1

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self._session.savepoint_committed = True


class _FakeSession:
    def __init__(self, row: Any = None) -> None:
        self._row = row
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.savepoint_started = 0
        self.savepoint_committed = False

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._row)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def begin_nested(self) -> _FakeNested:
        return _FakeNested(self)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _stamped_row() -> Any:
    mapping = ConventionsStandard(waivers=[], infra=["Dockerfile*"]).model_dump(
        mode="json"
    )
    mapping["cache_schema"] = _CACHE_SCHEMA_VERSION
    return cast("Any", type("Row", (), {"effective_map": mapping, "status": "ok"}))


def _pre_stamp_row() -> Any:
    # A row written before the stamp existed: a flat model dump, no
    # ``cache_schema`` key, and no ``infra`` in the payload.
    mapping = ConventionsStandard(waivers=[]).model_dump(mode="json")
    return cast("Any", type("Row", (), {"effective_map": mapping, "status": "ok"}))


@pytest.mark.asyncio
async def test_current_row_is_served_and_not_purged() -> None:
    row = _stamped_row()
    session = _FakeSession(row)
    svc = ConventionsService(session=cast("Any", session))

    got = await svc._cache_get(uuid4(), "deadbeef")

    assert got is row
    assert session.deleted == []
    assert session.savepoint_started == 0


@pytest.mark.asyncio
async def test_pre_stamp_row_is_stale_and_purged() -> None:
    row = _pre_stamp_row()
    session = _FakeSession(row)
    svc = ConventionsService(session=cast("Any", session))

    got = await svc._cache_get(uuid4(), "deadbeef")

    # Treated as absent so the next _cache_put repopulates; otherwise the
    # stale row would swallow every fresh insert as a benign duplicate and
    # force a re-derive on every read until HEAD moves.
    assert got is None
    assert session.deleted == [row]
    # The purge is a best-effort write on a shared session: savepoint, not
    # a bare delete that could poison the outer transaction.
    assert session.savepoint_started == 1
    assert session.savepoint_committed is True


@pytest.mark.asyncio
async def test_cache_put_embeds_schema_stamp() -> None:
    session = _FakeSession()
    svc = ConventionsService(session=cast("Any", session))

    mapping = ConventionsStandard(waivers=[], infra=["gitops/**"])
    await svc._cache_put(uuid4(), "deadbeef", mapping, "ok")

    payload = session.added[0].effective_map
    assert payload["cache_schema"] == _CACHE_SCHEMA_VERSION
    assert payload["infra"] == ["gitops/**"]
    # The stamp is inert on model reads: extra="ignore" tolerates the key.
    assert ConventionsStandard.model_validate(payload).infra == ["gitops/**"]


def test_staleness_predicate_shapes() -> None:
    assert _cache_row_current(_stamped_row()) is True
    assert _cache_row_current(_pre_stamp_row()) is False
    # A malformed (non-dict) payload reads as stale, never as current.
    junk = cast("Any", type("Row", (), {"effective_map": "not-a-dict"}))
    assert _cache_row_current(junk) is False
