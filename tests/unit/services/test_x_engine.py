"""XEngine coverage: release-post drafting + mentions poll, held/deduped/capped.

Mirrors the release-manager engine tests: flag-gated, no-creds no-ops, drafts
via the local model (mocked here), enforces the 280-char limit, dedupes
mentions by id, and caps origination per cycle + rolling open count. Never
posts — asserted against a real Postgres DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services import x_engine as x_engine_module
from roboco.services.task import X_POST_SOURCE, X_REPLY_SOURCE, get_task_service
from roboco.services.x_client import MAX_TWEET_CHARS, XClient, XMention, XPostResult
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2
_VERSION = "0.17.0"


class _FakeClient(XClient):
    """A configured stub — never touches the network."""

    def __init__(self, mentions: list[XMention] | None = None) -> None:
        self._mentions = mentions or []
        self.posted: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    async def post_tweet(self, text: str) -> XPostResult:
        self.posted.append(text)
        return XPostResult(posted=True, tweet_id="1", detail="ok")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return self._mentions


class _NullClient(XClient):
    @property
    def configured(self) -> bool:
        return False

    async def post_tweet(self, text: str) -> XPostResult:
        _ = text
        return XPostResult(posted=False, tweet_id=None, detail="no creds")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    existing = await session.execute(
        select(ProjectTable).where(ProjectTable.slug == SLUG)
    )
    if existing.scalar_one_or_none() is None:
        session.add(
            ProjectTable(
                name="RoboCo",
                slug=SLUG,
                git_url="https://github.com/x/roboco.git",
                default_branch="master",
                protected_branches=["master"],
                assigned_cell=Team.BACKEND,
                created_by=SYSTEM_UUID,
                is_active=True,
            )
        )
    await session.flush()


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(cfg, "x_engine_enabled", True)
    monkeypatch.setattr(cfg, "x_replies_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "x_max_open_posts", 10)
    monkeypatch.setattr(cfg, "x_mentions_max_per_cycle", 5)
    monkeypatch.setattr(cfg, "x_mentions_min_engagement", 0)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)


def _mock_local_model(monkeypatch: pytest.MonkeyPatch, reply: str | None) -> AsyncMock:
    mock = AsyncMock(return_value=reply)
    monkeypatch.setattr(x_engine_module, "_chat", mock)
    return mock


# --------------------------------------------------------------------------- #
# Release-post drafting
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_drafts_no_release_post(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    task = await engine.draft_release_post(version=_VERSION, highlights=["feat: x"])
    assert task is None
    assert await get_task_service(db_session).list_open_x_posts() == []


@pytest.mark.asyncio
async def test_no_credentials_drafts_no_release_post(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = x_engine_module.XEngine(db_session, client=_NullClient())
    task = await engine.draft_release_post(version=_VERSION, highlights=["feat: x"])
    assert task is None
    assert await get_task_service(db_session).list_open_x_posts() == []


@pytest.mark.asyncio
async def test_draft_release_post_holds_one_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "RoboCo just shipped a great new feature!")
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    task = await engine.draft_release_post(
        version=_VERSION, highlights=["feat: new thing"]
    )
    assert task is not None
    assert task.status == TS.PENDING
    assert task.confirmed_by_human is False
    assert task.assigned_to == SECRETARY_UUID
    assert task.source == X_POST_SOURCE
    assert markers.get_x_release_version(task) == _VERSION
    body = markers.get_x_draft_body(task)
    assert body is not None
    assert "RoboCo" in body


@pytest.mark.asyncio
async def test_release_post_body_enforces_280_chars(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "x" * 500)  # a runaway local-model draft
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    task = await engine.draft_release_post(version=_VERSION, highlights=[])
    assert task is not None
    body = markers.get_x_draft_body(task)
    assert body is not None
    assert len(body) <= MAX_TWEET_CHARS


@pytest.mark.asyncio
async def test_release_post_falls_back_when_local_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    mock = AsyncMock(side_effect=RuntimeError("ollama down"))
    monkeypatch.setattr(x_engine_module, "_chat", mock)
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    task = await engine.draft_release_post(
        version=_VERSION, highlights=["feat: still works"]
    )
    assert task is not None
    body = markers.get_x_draft_body(task)
    assert body is not None
    assert _VERSION in body


@pytest.mark.asyncio
async def test_draft_release_post_dedupes_same_version(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "shipped!")
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    await engine.draft_release_post(version=_VERSION, highlights=[])
    await engine.draft_release_post(version=_VERSION, highlights=[])
    open_posts = await get_task_service(db_session).list_open_x_posts()
    assert len(open_posts) == ONE


@pytest.mark.asyncio
async def test_draft_release_post_respects_open_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, x_max_open_posts=1)
    _mock_local_model(monkeypatch, "shipped!")
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    await engine.draft_release_post(version="1.0.0", highlights=[])
    task = await engine.draft_release_post(version="2.0.0", highlights=[])
    assert task is None
    open_posts = await get_task_service(db_session).list_open_x_posts()
    assert len(open_posts) == ONE


# --------------------------------------------------------------------------- #
# Mentions poll
# --------------------------------------------------------------------------- #


def _mention(
    mid: str, text: str = "great work @roboco", engagement: int = 1
) -> XMention:
    return XMention(
        id=mid,
        author_id="author-1",
        text=text,
        like_count=engagement,
        reply_count=0,
        retweet_count=0,
    )


@pytest.mark.asyncio
async def test_disabled_run_cycle_originates_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1")])
    )
    result = await engine.run_cycle()
    assert result == []


@pytest.mark.asyncio
async def test_run_cycle_noop_when_replies_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine on but the mention-reply sub-switch off: the poll cycle drafts
    nothing (release posting is a separate, still-enabled path)."""
    await _seed(db_session)
    _enable(monkeypatch, x_replies_enabled=False)
    _mock_local_model(monkeypatch, "Thanks!")
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1")])
    )
    result = await engine.run_cycle()
    assert result == []


@pytest.mark.asyncio
async def test_release_post_holds_even_when_replies_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release posting is decoupled from the mention-reply sub-switch: a
    release draft is still held with replies off."""
    await _seed(db_session)
    _enable(monkeypatch, x_replies_enabled=False)
    _mock_local_model(monkeypatch, "RoboCo shipped something great!")
    engine = x_engine_module.XEngine(db_session, client=_FakeClient())
    task = await engine.draft_release_post(version=_VERSION, highlights=["feat: x"])
    assert task is not None
    assert task.source == X_POST_SOURCE


@pytest.mark.asyncio
async def test_no_credentials_run_cycle_originates_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = x_engine_module.XEngine(db_session, client=_NullClient())
    result = await engine.run_cycle()
    assert result == []


@pytest.mark.asyncio
async def test_meaningful_mention_holds_reply_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "Thanks so much for the shoutout!")
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1")])
    )
    result = await engine.run_cycle()
    assert len(result) == ONE
    task = result[0]
    assert task.source == X_REPLY_SOURCE
    assert task.confirmed_by_human is False
    assert task.assigned_to == SECRETARY_UUID
    ref = markers.get_x_mention_ref(task)
    assert ref is not None
    assert ref["id"] == "m1"


@pytest.mark.asyncio
async def test_bot_like_mention_is_filtered_out(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "reply")
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1", text="RT spam")])
    )
    result = await engine.run_cycle()
    assert result == []


@pytest.mark.asyncio
async def test_below_engagement_floor_is_filtered_out(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, x_mentions_min_engagement=5)
    _mock_local_model(monkeypatch, "reply")
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1", engagement=1)])
    )
    result = await engine.run_cycle()
    assert result == []


@pytest.mark.asyncio
async def test_mentions_dedupe_across_cycles(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "reply")
    client = _FakeClient(mentions=[_mention("m1")])
    engine = x_engine_module.XEngine(db_session, client=client)
    first = await engine.run_cycle()
    second = await engine.run_cycle()
    assert len(first) == ONE
    assert second == []  # same mention id -> already seen, not re-proposed
    open_posts = await get_task_service(db_session).list_open_x_posts()
    assert len(open_posts) == ONE


@pytest.mark.asyncio
async def test_mentions_per_cycle_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, x_mentions_max_per_cycle=2)
    _mock_local_model(monkeypatch, "reply")
    mentions = [_mention(f"m{i}") for i in range(5)]
    engine = x_engine_module.XEngine(db_session, client=_FakeClient(mentions=mentions))
    result = await engine.run_cycle()
    assert len(result) == TWO


@pytest.mark.asyncio
async def test_mentions_respect_open_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, x_max_open_posts=1, x_mentions_max_per_cycle=5)
    _mock_local_model(monkeypatch, "reply")
    mentions = [_mention(f"cap{i}") for i in range(3)]
    engine = x_engine_module.XEngine(db_session, client=_FakeClient(mentions=mentions))
    result = await engine.run_cycle()
    assert len(result) == ONE


@pytest.mark.asyncio
async def test_reply_body_enforces_280_chars(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "y" * 400)
    engine = x_engine_module.XEngine(
        db_session, client=_FakeClient(mentions=[_mention("m1")])
    )
    result = await engine.run_cycle()
    assert len(result) == ONE
    body = markers.get_x_draft_body(result[0])
    assert body is not None
    assert len(body) <= MAX_TWEET_CHARS


@pytest.mark.asyncio
async def test_engine_never_calls_post_tweet(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither responsibility ever posts — that is XPostService's job only."""
    await _seed(db_session)
    _enable(monkeypatch)
    _mock_local_model(monkeypatch, "reply")
    client = _FakeClient(mentions=[_mention("m1")])
    engine = x_engine_module.XEngine(db_session, client=client)
    await engine.run_cycle()
    await engine.draft_release_post(version=_VERSION, highlights=[])
    assert client.posted == []
