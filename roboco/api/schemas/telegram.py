"""Schemas for the Telegram notifications bridge's CEO surface."""

from datetime import datetime

from pydantic import BaseModel, Field


class TelegramCredentialsStatus(BaseModel):
    """Whether the bot token + chat id are stored. Never the secrets themselves."""

    has_credentials: bool


class TelegramCredentialsSetRequest(BaseModel):
    """Set (or, if both are empty, clear) the bot token + chat id together."""

    bot_token: str = Field(default="")
    chat_id: str = Field(default="")


class TelegramWebAppAuthRequest(BaseModel):
    """A Telegram Mini App's raw ``initData`` handoff, pre-validation.

    Size-capped well above a real Telegram WebApp initData payload (query_id
    + user + auth_date + hash rarely exceeds a few hundred bytes) so an
    oversized body is rejected by the schema before it reaches HMAC work.
    """

    init_data: str = Field(min_length=1, max_length=4096)


# ==========================================================================
# Mini App "Today" brief — the cockpit's one-round-trip home screen.
# ==========================================================================


class TodayTaskItem(BaseModel):
    """One task row on the brief — just enough to recognize and jump."""

    id: str
    title: str
    status: str
    team: str | None = None
    updated_at: datetime | None = None


class TodayNeedsYou(BaseModel):
    """Everything currently waiting on the CEO, capped for a phone screen."""

    total: int
    awaiting_ceo_count: int
    awaiting_ceo: list[TodayTaskItem]
    blocked_count: int
    blocked: list[TodayTaskItem]
    held_drafts: dict[str, int]


class TodayFleetAgent(BaseModel):
    name: str
    role: str
    team: str | None = None
    task_title: str | None = None


class TodayFleet(BaseModel):
    total: int
    by_status: dict[str, int]
    working: list[TodayFleetAgent]


class TodaySpend(BaseModel):
    tokens_today: int
    cost_today_usd: float


class TodayShip(BaseModel):
    version: str
    open_release_proposal: bool
    ci_fix_tasks: int


class TelegramTodayResponse(BaseModel):
    """The whole brief in one response — the Mini App opens on this."""

    needs_you: TodayNeedsYou
    fleet: TodayFleet
    spend: TodaySpend
    ship: TodayShip
