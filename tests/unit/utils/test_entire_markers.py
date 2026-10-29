"""Entire's PR-body markers survive RoboCo's body rewrites."""

from __future__ import annotations

from roboco.utils.entire_markers import (
    SHADOW_PR_MARKER,
    entire_link_block,
    is_entire_pr_body,
    with_entire_markers,
)

_LINK = (
    "<!-- entire-trail-link-start -->\n"
    "https://entire.com/gh/acme/-/trails/7/changes/app/3\n"
    "<!-- entire-trail-link-end -->"
)
# A PR Entire opened itself, verbatim from a spike (PR #1169, Entire 0.12.2).
_ENTIRE_OPENED = (
    f"{_LINK}\n\nThis draft pull request was opened by [Entire](https://entire.com)"
    " to back the linked trail. Feel free to edit the title or body — the link"
    f" above is what keeps the trail and PR connected.\n\n{SHADOW_PR_MARKER}"
)


def test_the_link_block_is_found_whole() -> None:
    assert entire_link_block(_ENTIRE_OPENED) == _LINK
    assert entire_link_block("no markers") is None
    assert entire_link_block(None) is None


def test_either_marker_marks_an_entire_pr() -> None:
    assert is_entire_pr_body(_ENTIRE_OPENED)
    assert is_entire_pr_body(f"{_LINK}\n\nRoboCo's own body")
    assert is_entire_pr_body(f"body\n\n{SHADOW_PR_MARKER}")
    assert not is_entire_pr_body("plain body")
    assert not is_entire_pr_body(None)


def test_a_rewrite_keeps_the_link_on_top_and_the_shadow_marker_at_the_end() -> None:
    assert with_entire_markers("RoboCo body", _ENTIRE_OPENED) == (
        f"{_LINK}\n\nRoboCo body\n\n{SHADOW_PR_MARKER}"
    )


def test_a_body_without_markers_is_left_as_written() -> None:
    assert with_entire_markers("RoboCo body", "old body") == "RoboCo body"
    assert with_entire_markers("RoboCo body", None) == "RoboCo body"


def test_markers_already_present_are_not_duplicated() -> None:
    body = f"{_LINK}\n\nRoboCo body\n\n{SHADOW_PR_MARKER}"
    assert with_entire_markers(body, _ENTIRE_OPENED) == body
