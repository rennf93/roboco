"""roboco.services.research_errors — the readable reason a refusal carries.

The adapter tests in test_research.py pin each party's whole message; these pin
how a response body becomes a short readable reason: never markup, the first
page title, the visible text, a bounded length, and no crash on a body the
HTML parser chokes on.
"""

from __future__ import annotations

import httpx
import pytest
from roboco.services import research_errors
from roboco.services.research_errors import (
    ResearchSiteError,
    provider_status_error,
    site_status_error,
)

_URL = "https://app.example.com/pricing"
_REASON_MAX_CHARS = 160
_NONSTANDARD_STATUS = 520


def _response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body, request=httpx.Request("GET", _URL))


def _reason(message: str) -> str:
    """The text between the message's reason parentheses."""
    return message[message.index(" (") + 2 : message.rindex(")")]


def test_title_wins_over_text_and_an_inline_svg_title_is_not_the_page_title() -> None:
    body = (
        "<html><head><title>Access denied</title><title>second</title></head>"
        "<body><svg><title>icon</title></svg><p>You do not have access.</p>"
        "</body></html>"
    )
    error = site_status_error(_URL, _response(403, body))
    assert _reason(str(error)) == "page title: Access denied"


def test_text_skips_scripts_and_styles() -> None:
    body = (
        "<html><head><style>p{color:red}</style><script>var a = 1;</script>"
        "</head><body><noscript>enable js</noscript><p>Service paused.</p>"
        "</body></html>"
    )
    error = site_status_error(_URL, _response(503, body))
    assert _reason(str(error)) == "response text: Service paused."


def test_long_text_is_cut_to_the_reason_length_without_markup() -> None:
    body = "<div>" + "word " * 200 + "</div>"
    reason = _reason(str(site_status_error(_URL, _response(500, body))))
    text = reason.removeprefix("response text: ")
    assert len(text) <= _REASON_MAX_CHARS
    assert text.endswith("...")
    assert "<" not in reason


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param("", "empty response body", id="empty"),
        pytest.param(
            "<html><head><script>x()</script></head></html>",
            "no readable text in the response",
            id="markup-only",
        ),
    ],
)
def test_a_body_without_readable_text_says_so(body: str, reason: str) -> None:
    error = site_status_error(_URL, _response(403, body))
    assert _reason(str(error)) == reason


def test_a_status_without_a_standard_phrase_reads_as_its_number() -> None:
    error = provider_status_error(
        "searxng", "GET http://searxng.internal:8080/search", _response(520, "x")
    )
    assert f"answered HTTP {_NONSTANDARD_STATUS} to GET " in str(error)


def test_a_body_the_parser_chokes_on_still_yields_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older CPython parsers raise AssertionError on malformed ``<![``
    sections; the refusal must still be reported, not turned into a crash."""

    def choke(_self: object, _data: str) -> None:
        raise AssertionError

    monkeypatch.setattr(research_errors._PageTextParser, "feed", choke)
    error = site_status_error(_URL, _response(403, "<![foo[ x ]]>"))
    assert isinstance(error, ResearchSiteError)
    assert _reason(str(error)) == "no readable text in the response"
