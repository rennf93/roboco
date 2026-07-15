"""Schemas for the Telegram notifications bridge's CEO surface."""

from pydantic import BaseModel, Field


class TelegramCredentialsStatus(BaseModel):
    """Whether the bot token + chat id are stored. Never the secrets themselves."""

    has_credentials: bool


class TelegramCredentialsSetRequest(BaseModel):
    """Set (or, if both are empty, clear) the bot token + chat id together."""

    bot_token: str = Field(default="")
    chat_id: str = Field(default="")
