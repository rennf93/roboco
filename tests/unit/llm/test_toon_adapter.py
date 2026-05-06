"""roboco.llm.toon_adapter coverage — encode/decode/format helpers.

The adapter is a thin wrapper around the `toon` library — tests cover
the wrapper logic: dict/list/Pydantic encoding, JSON fallback on TOON
decode error, total failure path, format helpers, and singleton getter.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel
from roboco.llm.toon_adapter import ToonAdapter, _AdapterHolder, get_toon_adapter
from roboco.models.llm import ToonConfig

_PERCENT_FLOOR = 0.0


class _Pydantic(BaseModel):
    name: str
    age: int


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------


def test_encode_dict_returns_string() -> None:
    adapter = ToonAdapter()
    out = adapter.encode({"a": 1, "b": 2})
    assert isinstance(out, str)
    assert len(out) > 0


def test_encode_list_returns_string() -> None:
    adapter = ToonAdapter()
    out = adapter.encode([{"x": 1}, {"x": 2}])
    assert isinstance(out, str)


def test_encode_pydantic_calls_model_dump() -> None:
    adapter = ToonAdapter()
    out = adapter.encode(_Pydantic(name="alice", age=30))
    assert "alice" in out


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------


def test_decode_round_trip() -> None:
    adapter = ToonAdapter()
    src: dict[str, Any] = {"a": 1, "b": "two"}
    encoded = adapter.encode(src)
    decoded = adapter.decode(encoded)
    assert decoded == src


def test_decode_json_fallback_when_toon_fails() -> None:
    """When TOON decode raises, JSON fallback kicks in."""
    adapter = ToonAdapter()
    raw = json.dumps({"a": 1})
    with patch("roboco.llm.toon_adapter.toon.decode", side_effect=ValueError("nope")):
        out = adapter.decode(raw)
    assert out == {"a": 1}


def test_decode_raises_when_both_toon_and_json_fail() -> None:
    adapter = ToonAdapter()
    with (
        patch("roboco.llm.toon_adapter.toon.decode", side_effect=ValueError("nope")),
        pytest.raises(ValueError, match="Failed to decode"),
    ):
        adapter.decode("this is neither toon nor json")


# ---------------------------------------------------------------------------
# format helpers
# ---------------------------------------------------------------------------


def test_format_for_prompt_includes_label() -> None:
    adapter = ToonAdapter()
    out = adapter.format_for_prompt("Task Context", {"id": 1})
    assert out.startswith("Task Context:\n")


def test_format_tabular_request_basic() -> None:
    adapter = ToonAdapter()
    out = adapter.format_tabular_request(
        fields=["id", "name"],
        description="List the users",
    )
    assert "id,name" in out
    assert "List the users" in out


def test_format_tabular_request_with_examples() -> None:
    adapter = ToonAdapter()
    out = adapter.format_tabular_request(
        fields=["id", "name"],
        description="x",
        example_rows=[["1", "alice"], ["2", "bob"]],
    )
    assert "alice" in out
    assert "bob" in out


# ---------------------------------------------------------------------------
# estimate_token_savings
# ---------------------------------------------------------------------------


def test_estimate_token_savings_returns_three_tuple() -> None:
    adapter = ToonAdapter()
    json_chars, toon_chars, savings = adapter.estimate_token_savings(
        {"a": 1, "b": 2, "c": 3}
    )
    assert json_chars > 0
    assert toon_chars > 0
    assert isinstance(savings, float)


def test_estimate_token_savings_handles_zero_json_chars() -> None:
    """Empty data: JSON is `{}` (2 chars) so the divide-by-zero branch is
    only reachable by mocking. Cover the guard explicitly."""
    adapter = ToonAdapter()
    with patch("roboco.llm.toon_adapter.json.dumps", return_value=""):
        json_chars, _toon_chars, savings = adapter.estimate_token_savings({})
    assert json_chars == 0
    assert savings == _PERCENT_FLOOR


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


def test_get_toon_adapter_returns_singleton() -> None:
    _AdapterHolder.instance = None
    a = get_toon_adapter()
    b = get_toon_adapter()
    assert a is b


def test_get_toon_adapter_uses_default_config() -> None:
    _AdapterHolder.instance = None
    a = get_toon_adapter()
    assert isinstance(a.config, ToonConfig)
