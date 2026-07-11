"""Shared vault-note text policy: RoboCo's own feedback-callout convention,
the content hash that ignores it, and the frontmatter/body split.

Both the intake watcher (appends a "drafted" callout) and the KB-ingest
engine (appends a "quarantined" callout) write a one-line Obsidian callout
back into the note they just processed. Neither append may itself change
what the *next* scan considers "changed" — so both hash the note with every
RoboCo callout stripped first, via this one shared pattern. Both also parse
arbitrary CEO-authored markdown (a different trust/shape boundary than the
projection core's own generated notes), so the frontmatter split lives here
rather than in either engine.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import yaml

# Matches any RoboCo feedback callout (``> [!info] RoboCo: drafted ...``,
# ``> [!warning] RoboCo: quarantined ...``, ...) through end of line —
# generalized over the callout TYPE so either engine's convention is stripped
# by the other's hash comparison too.
FEEDBACK_CALLOUT_RE = re.compile(r"\n?> \[!\w+\] RoboCo: .*(?:\n|$)")

# YAML frontmatter block at the very start of the file.
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def content_hash(raw_text: str) -> str:
    """Sha256 of ``raw_text`` with every RoboCo feedback callout stripped, so
    appending one after processing doesn't change the effective hash."""
    stable = FEEDBACK_CALLOUT_RE.sub("", raw_text)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter dict + body, or ({}, text) with no frontmatter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    loaded = yaml.safe_load(m.group(1))
    return (loaded if isinstance(loaded, dict) else {}), text[m.end() :]
