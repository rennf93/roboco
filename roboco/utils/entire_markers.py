"""Entire's markers in a pull request body, kept across RoboCo's rewrites.

Entire ties a PR to its trail change with a link block at the top of the
body (``<!-- entire-trail-link-start -->`` url ``<!-- entire-trail-link-end
-->``), and marks a PR it opened itself with ``<!-- entire-shadow-pr -->``.
The change stays linked by branch without them, but the block is the PR's
only visible link to its trail and Entire never puts it back, so a body
RoboCo writes keeps both.
"""

from __future__ import annotations

import re

_LINK_BLOCK = re.compile(
    r"<!-- entire-trail-link-start -->.*?<!-- entire-trail-link-end -->", re.DOTALL
)
SHADOW_PR_MARKER = "<!-- entire-shadow-pr -->"


def entire_link_block(body: str | None) -> str | None:
    """The body's trail link block, or None."""
    match = _LINK_BLOCK.search(body or "")
    return match.group(0) if match else None


def is_entire_pr_body(body: str | None) -> bool:
    """Whether Entire has marked this PR body as a trail change's."""
    return entire_link_block(body) is not None or SHADOW_PR_MARKER in (body or "")


def with_entire_markers(new_body: str, old_body: str | None) -> str:
    """``new_body`` carrying ``old_body``'s Entire markers: the link block
    on top, the shadow marker at the end. Markers ``new_body`` already
    carries are not duplicated."""
    body = new_body
    block = entire_link_block(old_body)
    if block and block not in body:
        body = f"{block}\n\n{body}"
    if SHADOW_PR_MARKER in (old_body or "") and SHADOW_PR_MARKER not in body:
        body = f"{body}\n\n{SHADOW_PR_MARKER}"
    return body
