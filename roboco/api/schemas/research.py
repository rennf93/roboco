"""Web-research API schemas."""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """A web search request."""

    query: str = Field(min_length=1, max_length=2000)
    max_results: int | None = Field(default=None, ge=1, le=20)


class SearchResultItem(BaseModel):
    """One normalised search result."""

    title: str
    url: str
    snippet: str
    score: float | None = None


class SearchResponse(BaseModel):
    """Normalised search results plus an optional synthesized answer."""

    query: str
    provider: str
    answer: str | None = None
    results: list[SearchResultItem]


class FetchRequest(BaseModel):
    """A request to extract readable content for a URL."""

    url: str = Field(min_length=1, max_length=4000)
    max_chars: int | None = Field(default=None, ge=1)


class FetchResponse(BaseModel):
    """Extracted page content (possibly truncated to the configured cap)."""

    url: str
    provider: str
    content: str
    truncated: bool
