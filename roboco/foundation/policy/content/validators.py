"""Shared validation primitives for structured agent content.

Two jobs:

- ``reject_trivial`` — the non-empty / non-placeholder gate, reused by every
  content model's field validators. Raises ``ValueError`` so it composes with
  Pydantic field validators (which collect ``ValueError`` into a
  ``ValidationError``).
- ``ContentValidationError`` — the public, gateway-facing exception. The
  top-level ``validate_content`` (in :mod:`.models`) converts Pydantic's
  ``ValidationError`` into this so callers get a single ``(field, reason)``
  shape to build a remediation envelope from.
"""

from __future__ import annotations

import string
from typing import Any

# Placeholder tokens that are never an acceptable whole-field value. Extends the
# commit-validator's banned single-word list (services/gateway/commit_validator).
BANNED_PHRASES: frozenset[str] = frozenset(
    {
        "wip",
        "tmp",
        "tbd",
        "todo",
        "asdf",
        "oops",
        "stuff",
        "things",
        "n/a",
        "na",
        "none",
        "null",
        "-",
        "--",
        "...",
        ".",
        "x",
    }
)


class ContentValidationError(Exception):
    """A structured-content payload failed validation.

    Carries the offending ``field`` and a human ``reason`` so the gateway can
    return a remediable envelope (``{error, message, remediate, missing}``).
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _all_tokens_filler(text: str) -> bool:
    """True when every whitespace token (sans edge punctuation) is a placeholder.

    Catches multi-token soup that no single check would — ``wip wip``,
    ``tbd / na``, ``todo todo todo`` — without flagging real prose that merely
    *contains* a filler word (``none of the tests failed``). Pure-punctuation
    tokens (``/``) strip to empty and are dropped before the all-filler test.
    """
    meaningful = [
        stripped
        for tok in text.split()
        if (stripped := tok.strip(string.punctuation).lower())
    ]
    return bool(meaningful) and all(tok in BANNED_PHRASES for tok in meaningful)


def reject_trivial(value: str, *, field: str, min_chars: int = 1) -> str:
    """Return the trimmed value, or raise ``ValueError`` if it is trivial.

    Trivial = empty, shorter than ``min_chars``, a known placeholder token, or a
    string whose every token is a placeholder (``wip wip``). Raises
    ``ValueError`` (not ``ContentValidationError``) so it can be used directly
    inside Pydantic field validators.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) < min_chars:
        raise ValueError(f"{field} must be at least {min_chars} characters")
    if text.lower() in BANNED_PHRASES or _all_tokens_filler(text):
        raise ValueError(f"{field} must not be placeholder text (got {value!r})")
    return text


def coerce_to_list(value: Any) -> Any:
    """Wrap a lone scalar/dict into a one-element list; pass lists/None through.

    Mirrors ``api.schemas.v1.do._coerce_to_list``: an agent that passes a single
    string (or dict) where a list is declared is making the well-intentioned
    single-item mistake — wrap it rather than reject it.
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str | dict):
        return [value]
    return value


# Keys an LLM's tool input may wrap a piece of text under. The Claude SDK parses
# XML-ish mixed content the model sometimes emits for a list-of-strings field
# (e.g. ``<item>…</item>``) into ``{"item": {"$text": "…"}}``; ``$text`` is the
# SDK's marker for element text content. Ordered: most specific first.
_TEXT_KEYS: tuple[str, ...] = ("$text", "text", "item", "value", "content", "summary")


def _extract_strs(item: Any) -> list[str]:
    """Pull every string out of one list element, recursing through wrappers."""
    if isinstance(item, str):
        stripped = item.strip()
        return [stripped] if stripped else []
    if isinstance(item, dict):
        return _extract_strs_from_dict(item)
    if isinstance(item, list):
        out: list[str] = []
        for sub in item:
            out.extend(_extract_strs(sub))
        return out
    return []


def _extract_strs_from_dict(item: dict) -> list[str]:
    """Resolve a dict element to strings via the first recognized text key,
    else any bare string values it carries (recurses through SDK ``$text`` wrappers)."""
    for key in _TEXT_KEYS:
        if key in item:
            return _extract_strs(item[key])
    return [str(v).strip() for v in item.values() if isinstance(v, str) and v.strip()]


def coerce_str_list(value: Any) -> list[str]:
    """Coerce a list-of-strings field to a flat ``list[str]``.

    An LLM may emit a ``list[str]`` field (``acceptance_criteria``, ``notes``,
    ``what_this_builds``, a work unit's ``items``) as XML-ish ``<item>…</item>``
    elements, which the Claude SDK parses into ``[{"item": {"$text": "…"}}, …]``.
    A bare dict/scalar is the single-item case. This recurses through the
    wrappers and returns only strings — never a dict — so the value is safe for a
    ``VARCHAR[]`` column (asyncpg rejects non-str elements with a DataError) and
    for markdown rendering (``str(dict)`` would otherwise dump ``"{'item': …}"``).
    Anything that cannot be reduced to a string is dropped.
    """
    if value is None:
        return []
    if isinstance(value, str | dict):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        out.extend(_extract_strs(item))
    return out
