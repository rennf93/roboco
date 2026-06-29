"""/usage/sync contains transcript_path under the Claude projects dir.

The endpoint is an unauthenticated container-internal HTTP surface; a forged
``transcript_path`` could ``stat`` / read arbitrary files. The guard requires
a ``.jsonl`` suffix and that the resolved path stays under the transcript
root (``ROBOCO_TRANSCRIPT_DIR``, default ``~/.claude/projects``). Existing
behavior — a missing-but-contained transcript returns 200 with zeroed totals
— is preserved.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import roboco.agent_sdk.server as srv
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_BAD = 400
_OK = 200


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    srv._state.reset()
    yield
    srv._state.reset()


@pytest.fixture(autouse=True)
def _transcript_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Contain transcripts under tmp_path so the guard's base is the test dir.
    monkeypatch.setenv("ROBOCO_TRANSCRIPT_DIR", str(tmp_path))


@pytest.fixture
def client() -> TestClient:
    return TestClient(srv.app)


_IN_TOKENS = 10
_OUT_TOKENS = 2


def _assistant_line() -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": _IN_TOKENS,
                    "output_tokens": _OUT_TOKENS,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def test_rejects_non_jsonl_path(client: TestClient) -> None:
    """``/etc/passwd`` has no .jsonl suffix → 400, never stat'd."""
    resp = client.post("/usage/sync", json={"transcript_path": "/etc/passwd"})
    assert resp.status_code == _BAD


def test_rejects_absolute_path_outside_base(
    client: TestClient, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "leak.jsonl"
    outside.write_text("{}", encoding="utf-8")
    resp = client.post("/usage/sync", json={"transcript_path": str(outside)})
    assert resp.status_code == _BAD


def test_rejects_traversal_escape(client: TestClient, tmp_path: Path) -> None:
    """A .jsonl path that resolves outside the base → 400."""
    escape = tmp_path / ".." / "secret.jsonl"
    resp = client.post("/usage/sync", json={"transcript_path": str(escape)})
    assert resp.status_code == _BAD


def test_rejects_empty_path(client: TestClient) -> None:
    resp = client.post("/usage/sync", json={"transcript_path": ""})
    assert resp.status_code == _BAD


def test_rejects_nul_in_path(client: TestClient) -> None:
    resp = client.post("/usage/sync", json={"transcript_path": "be\x00evil.jsonl"})
    assert resp.status_code == _BAD


def test_accepts_transcript_under_base(client: TestClient, tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_assistant_line() + "\n", encoding="utf-8")
    resp = client.post("/usage/sync", json={"transcript_path": str(transcript)})
    assert resp.status_code == _OK
    assert resp.json()["tokens_input"] == _IN_TOKENS


def test_missing_transcript_under_base_returns_200(
    client: TestClient, tmp_path: Path
) -> None:
    """A contained-but-not-yet-written transcript still returns 200 (graceful)."""
    resp = client.post(
        "/usage/sync", json={"transcript_path": str(tmp_path / "nope.jsonl")}
    )
    assert resp.status_code == _OK
    assert resp.json()["tokens_input"] == 0
