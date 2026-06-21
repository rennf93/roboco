"""Tests for the shared content validators (``reject_trivial``).

``reject_trivial`` is the single non-empty / non-placeholder gate reused by
every structured-content field validator AND the gateway anti-soup guards. It
must reject: empty, too-short, a lone placeholder token, AND a string whose
every whitespace token is a placeholder (``wip wip``, ``tbd / na``).
"""

from __future__ import annotations

import pytest
from roboco.foundation.policy.content.validators import reject_trivial


def test_returns_trimmed_value_when_substantive() -> None:
    assert reject_trivial("  real content here  ", field="x") == "real content here"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_rejects_empty(blank: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        reject_trivial(blank, field="summary")


def test_rejects_too_short() -> None:
    with pytest.raises(ValueError, match="at least 10 characters"):
        reject_trivial("short", field="summary", min_chars=10)


@pytest.mark.parametrize(
    "token", ["wip", "TBD", "asdf", "n/a", "none", "-", "...", "x"]
)
def test_rejects_lone_placeholder_token(token: str) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        reject_trivial(token, field="summary")


@pytest.mark.parametrize(
    "soup",
    ["wip wip", "tbd / na", "todo todo todo", "asdf asdf asdf asdf", "none none"],
)
def test_rejects_all_filler_token_string(soup: str) -> None:
    # Every token is a placeholder — the whole string is soup even though it is
    # neither a single banned token nor below the length floor.
    with pytest.raises(ValueError, match="placeholder"):
        reject_trivial(soup, field="summary")


@pytest.mark.parametrize(
    "ok",
    [
        "none of the tests failed",  # 'none' present but not all-filler
        "fixed the x coordinate bug",  # 'x' present but not all-filler
        "rate_limited",  # a real substitute reason
        "LGTM, merging now",
    ],
)
def test_accepts_real_text_containing_a_filler_word(ok: str) -> None:
    assert reject_trivial(ok, field="reason", min_chars=3) == ok.strip()
