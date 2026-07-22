"""codex_auth — keep the Codex CLI credential live via the OAuth refresh grant.

Unlike grok's bundle (keyed by ``<issuer>::<client_id>``, carrying its own
``expires_at``), the Codex auth.json is flat — ``{tokens: {access_token,
refresh_token, ...}}`` — and staleness is decided purely by decoding the
access token's JWT ``exp`` claim.
"""

from __future__ import annotations

import base64
import json
import pathlib
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from roboco.llm.providers import codex_auth as ca

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _jwt(exp_unix: int) -> str:
    """Build a minimal JWT (header.payload.signature) carrying an ``exp`` claim."""
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp_unix}).encode())
        .rstrip(b"=")
        .decode()
    )
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _bundle(access_token: str, *, refresh_token: str = "rt") -> dict[str, Any]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": "id-tok",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": "acct-1",
        },
        "last_refresh": "2026-01-01T00:00:00Z",
    }


def _write(path: Path, bundle: dict[str, Any]) -> None:
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _exp(delta: timedelta) -> int:
    return int((datetime.now(UTC) + delta).timestamp())


def test_seconds_until_expiry_and_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    token = _jwt(_exp(timedelta(minutes=54)))
    _write(path, _bundle(token))
    remaining = ca.seconds_until_expiry(path)
    assert remaining is not None
    assert 3100 < remaining < 3300  # noqa: PLR2004 — ~54 minutes
    assert ca.is_valid(path)
    assert not ca.is_valid(path, skew_seconds=3600)  # <1h left, 1h skew fails


def test_seconds_until_expiry_none_for_missing_or_entryless(tmp_path: Path) -> None:
    assert ca.seconds_until_expiry(tmp_path / "nope.json") is None
    path = tmp_path / "auth.json"
    _write(path, {"tokens": {"account_id": "x"}})  # no access_token
    assert ca.seconds_until_expiry(path) is None


def test_seconds_until_expiry_none_for_non_jwt_access_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle("api-key-not-a-jwt"))
    assert ca.seconds_until_expiry(path) is None


def test_refresh_skips_when_fresh(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(hours=6)))))
    calls: list[str] = []

    def _post(url: str, _form: dict[str, str]) -> dict[str, Any]:
        calls.append(url)
        return {}

    assert ca.refresh_if_stale(path, post=_post) == "fresh"
    assert not calls  # no network call when the token is still valid


def test_refresh_mints_new_token_when_stale(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(minutes=-5)))))
    new_access = _jwt(_exp(timedelta(hours=6)))

    def _post(url: str, form: dict[str, str]) -> dict[str, Any]:
        assert url == "https://auth.openai.com/oauth/token"
        assert form["grant_type"] == "refresh_token"
        assert form["refresh_token"] == "rt"
        assert form["client_id"]
        return {
            "access_token": new_access,
            "refresh_token": "new-rt",
            "id_token": "new-id",
        }

    assert ca.refresh_if_stale(path, post=_post) == "refreshed"
    bundle = json.loads(path.read_text())
    assert bundle["tokens"]["access_token"] == new_access
    assert bundle["tokens"]["refresh_token"] == "new-rt"  # rotated
    assert bundle["tokens"]["id_token"] == "new-id"
    assert bundle["last_refresh"] != "2026-01-01T00:00:00Z"
    assert ca.is_valid(path)


def test_refresh_keeps_old_refresh_token_when_response_omits_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(minutes=-1)))))

    def _post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {"access_token": _jwt(_exp(timedelta(hours=6)))}  # no refresh_token

    assert ca.refresh_if_stale(path, post=_post) == "refreshed"
    assert json.loads(path.read_text())["tokens"]["refresh_token"] == "rt"


def test_refresh_missing_file(tmp_path: Path) -> None:
    assert ca.refresh_if_stale(tmp_path / "nope.json") == "missing"


def test_refresh_no_refresh_token_api_key_mode(tmp_path: Path) -> None:
    # auth_mode=apikey has no tokens/refresh_token — refresh is a graceful no-op.
    path = tmp_path / "auth.json"
    _write(path, {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"})
    assert ca.refresh_if_stale(path) == "no_refresh_token"


def test_refresh_failed_on_post_error_leaves_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    stale = _jwt(_exp(timedelta(minutes=-1)))
    _write(path, _bundle(stale))

    def _boom(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        raise RuntimeError("network down")

    assert ca.refresh_if_stale(path, post=_boom) == "failed"
    assert json.loads(path.read_text())["tokens"]["access_token"] == stale


def test_refresh_failed_when_no_access_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(minutes=-1)))))
    assert ca.refresh_if_stale(path, post=lambda _u, _f: {}) == "failed"


def test_refresh_persists_rotated_token_when_atomic_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rotated refresh_token is single-use; if the atomic write fails after
    rotation, the direct-write fallback must still land it on disk."""
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(minutes=-1)))))
    new_access = _jwt(_exp(timedelta(hours=6)))

    def _post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {"access_token": new_access, "refresh_token": "rotated-rt"}

    def _boom_replace(_self: pathlib.Path, _target: pathlib.Path) -> pathlib.Path:
        raise OSError("replace failed (simulated)")

    monkeypatch.setattr(pathlib.Path, "replace", _boom_replace)

    assert ca.refresh_if_stale(path, post=_post) == "refreshed"
    tokens = json.loads(path.read_text())["tokens"]
    assert tokens["refresh_token"] == "rotated-rt"
    assert tokens["access_token"] == new_access


def test_main_check_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(home / "auth.json", _bundle(_jwt(_exp(timedelta(hours=6)))))
    assert ca.main(["--check"]) == 0
    _write(home / "auth.json", _bundle(_jwt(_exp(timedelta(minutes=-1)))))
    assert ca.main(["--check"]) == 1


def test_concurrent_refresh_does_not_double_rotate_single_use_token(
    tmp_path: Path,
) -> None:
    """Two near-simultaneous ``refresh_if_stale`` calls must POST the
    refresh-token grant ONCE — a process-wide lock + re-load inside it makes
    the loser see the winner's refreshed token and return "fresh" instead of
    re-rotating (mirrors grok_auth's #94 fix)."""
    path = tmp_path / "auth.json"
    _write(path, _bundle(_jwt(_exp(timedelta(minutes=-1)))))
    posted: list[str] = []
    post_lock = threading.Lock()

    def _post(_url: str, form: dict[str, str]) -> dict[str, Any]:
        with post_lock:
            posted.append(form["refresh_token"])
        time.sleep(0.1)
        return {
            "access_token": _jwt(_exp(timedelta(hours=6))),
            "refresh_token": "new-rt",
        }

    results: list[str] = []

    def _run() -> None:
        results.append(ca.refresh_if_stale(path, post=_post))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(posted) == 1  # exactly one grant POST — no double rotation
    assert all(r in ("refreshed", "fresh") for r in results)
    assert "refreshed" in results
