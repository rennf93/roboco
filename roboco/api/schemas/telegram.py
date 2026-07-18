"""Schemas for the Telegram notifications bridge's CEO surface."""

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
