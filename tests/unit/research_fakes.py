"""Shared fakes for the web-research tests: DNS answers and refusal pages.

No research test may reach the network. The SearXNG fetch guard resolves the
target host before fetching, so tests that fetch a named host answer that
lookup from a table here instead of a real resolver.
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

# A globally routable address every named test host resolves to, so the guard
# lets the fetch through to the MockTransport (the SearXNG fetch tests already
# use it as an IP literal).
PUBLIC_IP = "93.184.216.34"

# Vercel's Security Checkpoint page. The opening is the prefix logged with the
# 2026-10-28 06:16:41Z refusal; the title is the one the checkpoint page
# carries in Vercel's community forum reports. The rest is illustrative.
VERCEL_CHECKPOINT_HTML = (
    "<html>\n  <head>\n    <style global>body{font-family:Arial,Helvetica,"
    "sans-serif}.container{align-items:center;display:flex;"
    "flex-direction:column}</style>\n"
    "    <title>Vercel Security Checkpoint</title>\n  </head>\n"
    "  <body><div class=container><p>We're verifying your browser</p></div>"
    "<script>window.__checkpoint=1</script></body>\n</html>"
)

# A plain server 404 page: no <title>, readable text in the body.
PLAIN_NOT_FOUND_HTML = (
    "<html><body><h1>Not Found</h1>"
    "<p>The requested URL was not found on this server.</p></body></html>"
)


def fake_dns(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> None:
    """Answer the running loop's host lookups from ``answers``.

    A host missing from the table fails the way an unknown name does.
    """

    async def getaddrinfo(host: str, *_args: Any, **_kwargs: Any) -> Any:
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)


# A page trafilatura extracts "Hello readable world." from.
READABLE_PAGE_HTML = (
    "<html><head><title>t</title></head><body>"
    "<nav>menu menu</nav><article><h1>Head</h1>"
    "<p>Hello readable world.</p></article></body></html>"
)

# One canned answer: (status, headers, body).
Page = tuple[int, dict[str, str], str]


def redirect(location: str, status: int = 302) -> Page:
    """A redirect answer pointing at ``location``."""
    return (status, {"location": location}, "")


def page(body: str, status: int = 200) -> Page:
    """An HTML answer."""
    return (status, {"content-type": "text/html"}, body)


def site_routes(
    pages: dict[str, Page], requested: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    """A MockTransport handler answering each URL from ``pages``.

    Every requested URL is appended to ``requested``, so a test can assert
    which hops were (and were not) requested. A URL missing from ``pages``
    answers 404.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        status, headers, body = pages.get(str(request.url), (404, {}, "not here"))
        return httpx.Response(status, headers=headers, text=body)

    return handler
