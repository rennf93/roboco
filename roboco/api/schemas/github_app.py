"""Schemas for the GitHub App integration's CEO-managed surface."""

from pydantic import BaseModel, Field


class GitHubAppCredentialsStatus(BaseModel):
    """Whether the App id + private key are stored. Never the key itself."""

    has_credentials: bool


class GitHubAppCredentialsSetRequest(BaseModel):
    """Set (or, if both are empty, clear) the App id + private key together."""

    app_id: str = Field(default="")
    private_key: str = Field(default="")


class InstallationResponse(BaseModel):
    """One App installation — enough for the panel's installation picker."""

    id: int
    account_login: str


class InstallationRepositoryResponse(BaseModel):
    """One repository visible to an installation — the "Select repo" list."""

    full_name: str
    clone_url: str
    private: bool
