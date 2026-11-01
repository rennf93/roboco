"""Research failures that say who refused a call and why.

A ``web_search`` / ``web_fetch`` call can fail at three different parties, and
the agent acts differently on each:

* the research provider (SearXNG, Tavily, Brave, Exa), RoboCo's search backend:
  it answered an error status or a body that is not a JSON object, or never
  answered. Another URL will not help.
* the target site of a direct page fetch (SearXNG's ``fetch`` downloads the page
  itself): it answered an error status or a bot-check challenge, or never
  answered. Another source for the same facts is the way forward.
* RoboCo's URL guard: the URL is not plain http(s) to a public host, so it was
  never fetched.

Each party has its own ``ResearchError`` subclass, and every message names the
party, what was being reached, and a short readable reason taken from the
response: the page ``<title>``, else its first readable text, never markup.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, override
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx


class ResearchError(Exception):
    """A web-research call failed; the subclass names the party that failed it."""


class ResearchUnsupportedError(ResearchError):
    """The active provider does not support the requested operation.

    Raised, for example, when ``web_fetch`` is called while the configured
    provider has no content-extraction endpoint (Brave). Distinct from a
    transient ``ResearchError`` so the route can map it to 501 rather than 502.
    """


class ResearchProviderError(ResearchError):
    """The research provider answered an error or a non-object body, or nothing."""


class ResearchSiteError(ResearchError):
    """The target site of a direct page fetch refused it or never answered."""


class ResearchUrlRefusedError(ResearchError):
    """RoboCo's URL guard refused a URL that is not http(s) to a public host."""


# A reason is a short pointer, not the page: long enough for a title or a
# one-line error message, short enough to read in a tool result.
_REASON_MAX_CHARS = 160

# Only the head of an error body is scanned for its title and first text. Both
# sit at the top of a page, and the cap bounds the parse of a huge error page.
_REASON_SCAN_CHARS = 65_536

# Elements whose text a reader never sees. ``svg`` is here because an inline
# icon's own ``<title>`` must not pass for the page title.
_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})

# Response headers a bot-mitigation layer sets on its challenge page, with the
# values that mean "challenge". Cloudflare documents ``cf-mitigated: challenge``
# on every challenge page (developers.cloudflare.com/cloudflare-challenges/
# challenge-types/challenge-pages/detect-response). AWS WAF documents
# ``x-amzn-waf-action: challenge`` (HTTP 202) and ``captcha`` (HTTP 405)
# (docs.aws.amazon.com/waf/latest/developerguide/
# waf-captcha-and-challenge-actions.html). Vercel's Security Checkpoint answers
# with ``x-vercel-mitigated: challenge``; Vercel's header reference does not
# list it, but its responses in Vercel's own community forum carry it.
_CHALLENGE_HEADERS: dict[str, tuple[str, frozenset[str]]] = {
    "cf-mitigated": ("Cloudflare", frozenset({"challenge"})),
    "x-amzn-waf-action": ("AWS WAF", frozenset({"challenge", "captcha"})),
    "x-vercel-mitigated": ("Vercel", frozenset({"challenge"})),
}

# Challenge pages recognised by their ``<title>``, for when no challenge header
# came back. The 2026-10-28 refusal that prompted these messages was Vercel's
# Security Checkpoint page, whose title is "Vercel Security Checkpoint".
_CHALLENGE_TITLES: dict[str, str] = {"vercel security checkpoint": "Vercel"}


class _PageTextParser(HTMLParser):
    """Collect a page's ``<title>`` texts and the visible text around them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.text_parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden_depth += 1
        elif tag == "title" and not self._hidden_depth:
            self._in_title = True
            self.titles.append("")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag == "title":
            self._in_title = False

    @override
    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        if self._in_title:
            self.titles[-1] += data
        else:
            self.text_parts.append(data)


@dataclass(frozen=True)
class _PageText:
    """The readable parts of a response body: its first title and its text."""

    title: str
    text: str


def _shortened(text: str) -> str:
    """Collapse whitespace and cut ``text`` to the reason length."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= _REASON_MAX_CHARS:
        return collapsed
    return collapsed[: _REASON_MAX_CHARS - 3].rstrip() + "..."


def _page_text(body: str) -> _PageText:
    """Read the first title and the visible text out of an HTML or plain body."""
    parser = _PageTextParser()
    # Older CPython releases raise AssertionError from HTMLParser on a
    # malformed ``<![`` section (``<![foo[`` does on 3.11.2; 3.13.18 parses
    # it). A page that trips it still yields whatever was read before it.
    with suppress(AssertionError):
        parser.feed(body[:_REASON_SCAN_CHARS])
        parser.close()
    title = parser.titles[0] if parser.titles else ""
    return _PageText(
        title=_shortened(title), text=_shortened(" ".join(parser.text_parts))
    )


def _readable_reason(body: str, page: _PageText) -> str:
    """The page title, else its first readable text, else what the body lacks."""
    if page.title:
        return f"page title: {page.title}"
    if page.text:
        return f"response text: {page.text}"
    if body.strip():
        return "no readable text in the response"
    return "empty response body"


def _status_line(response: httpx.Response) -> str:
    """``HTTP 403 Forbidden``; just ``HTTP 520`` for a code with no standard phrase."""
    return f"HTTP {response.status_code} {response.reason_phrase}".rstrip()


def _exception_kind(exc: Exception) -> str:
    """``ConnectError: <detail>``, or the bare class name when it carries none."""
    detail = str(exc).rstrip(". ")
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _challenge_vendor_from_headers(response: httpx.Response) -> str | None:
    """The bot-mitigation vendor whose challenge header the response carries."""
    for header, (vendor, values) in _CHALLENGE_HEADERS.items():
        if response.headers.get(header, "").strip().lower() in values:
            return vendor
    return None


def carries_challenge_header(response: httpx.Response) -> bool:
    """True when a bot-mitigation layer marked the response as its challenge page."""
    return _challenge_vendor_from_headers(response) is not None


def _challenge_vendor(response: httpx.Response, page: _PageText) -> str | None:
    """The vendor of the bot-check page this response is, from its headers or title."""
    from_headers = _challenge_vendor_from_headers(response)
    if from_headers is not None:
        return from_headers
    return _CHALLENGE_TITLES.get(page.title.lower())


def _bot_check_sentence(vendor: str | None) -> str:
    """The sentence telling the agent a bot check refused it, or nothing."""
    if vendor is None:
        return ""
    return (
        f" This is {vendor}'s bot-check (challenge) page: the site blocks "
        "automated fetches, so get this information from another source."
    )


def _redirect_note(response: httpx.Response) -> str:
    """Where the fetch ended up when the site redirected it, or nothing."""
    if not response.history:
        return ""
    return f" after a redirect to {response.url}"


def site_status_error(url: str, response: httpx.Response) -> ResearchSiteError:
    """The site answered a page fetch with an error status or a bot-check page."""
    page = _page_text(response.text)
    message = (
        f"Site {response.url.host} answered {_status_line(response)} to the page "
        f"fetch of {url}{_redirect_note(response)} "
        f"({_readable_reason(response.text, page)})."
        f"{_bot_check_sentence(_challenge_vendor(response, page))}"
    )
    return ResearchSiteError(message)


def site_unreachable_error(url: str, exc: Exception) -> ResearchSiteError:
    """The site never answered a page fetch (connection, TLS, timeout, redirects)."""
    host = urlparse(url).hostname or url
    return ResearchSiteError(
        f"No answer from site {host} to the page fetch of {url}: "
        f"{_exception_kind(exc)}."
    )


def provider_status_error(
    provider: str, endpoint: str, response: httpx.Response
) -> ResearchProviderError:
    """The research provider answered its own API call with an error status."""
    page = _page_text(response.text)
    return ResearchProviderError(
        f"Research provider {provider} answered {_status_line(response)} to "
        f"{endpoint} ({_readable_reason(response.text, page)})."
    )


def provider_body_error(
    provider: str, endpoint: str, response: httpx.Response
) -> ResearchProviderError:
    """The research provider answered with a body that is not a JSON object."""
    page = _page_text(response.text)
    content_type = response.headers.get("content-type") or "no content-type"
    return ResearchProviderError(
        f"Research provider {provider} answered {endpoint} with "
        f"{_status_line(response)} but a body that is not a JSON object "
        f"({content_type}; {_readable_reason(response.text, page)})."
    )


def provider_unreachable_error(
    provider: str, endpoint: str, exc: Exception
) -> ResearchProviderError:
    """The research provider never answered (connection, TLS, timeout)."""
    return ResearchProviderError(
        f"No answer from research provider {provider} to {endpoint}: "
        f"{_exception_kind(exc)}."
    )


def url_refused_error(url: str, why: str) -> ResearchUrlRefusedError:
    """RoboCo's URL guard refused ``url`` before any request left the server."""
    return ResearchUrlRefusedError(f"RoboCo's URL guard refused {url!r}: {why}.")
