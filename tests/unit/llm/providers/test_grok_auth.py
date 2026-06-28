"""grok_auth — keep the SuperGrok token live via the OAuth refresh-token grant."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from roboco.llm.providers import grok_auth as ga

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_PAST = "2020-01-01T00:00:00.000000000Z"
_FUTURE = "2099-01-01T00:00:00.000000000Z"
_CLIENT = "b1a00492-client"


def _bundle(expires_at: str, *, refresh_token: str = "rt") -> dict[str, Any]:
    return {
        f"https://auth.x.ai::{_CLIENT}": {
            "key": "old-access",
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": _CLIENT,
        }
    }


def _write(path: Path, bundle: dict[str, Any]) -> None:
    path.write_text(json.dumps(bundle), encoding="utf-8")


def test_parse_timestamp_handles_nanosecond_z() -> None:
    parsed = ga._parse_timestamp("2026-06-19T06:54:18.840268518Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert ga._parse_timestamp("not a date") is None
    assert ga._parse_timestamp("") is None


def test_seconds_until_expiry_and_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    now = datetime(2026, 6, 19, 6, 0, tzinfo=UTC)  # 54m before the 06:54 expiry
    _write(path, _bundle("2026-06-19T06:54:18.840268518Z"))
    remaining = ga.seconds_until_expiry(path, now=now)
    assert remaining is not None
    assert 3200 < remaining < 3300  # noqa: PLR2004 — ~54 minutes
    assert ga.is_valid(path, now=now)
    # Less than an hour left -> not valid under a 1h skew.
    assert not ga.is_valid(path, skew_seconds=3600, now=now)


def test_seconds_until_expiry_none_for_missing_or_entryless(tmp_path: Path) -> None:
    assert ga.seconds_until_expiry(tmp_path / "nope.json") is None
    path = tmp_path / "auth.json"
    _write(path, {"x": {"no_refresh_token": True}})
    assert ga.seconds_until_expiry(path) is None


def test_refresh_skips_when_fresh(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_FUTURE))
    calls: list[str] = []

    def _post(url: str, _form: dict[str, str]) -> dict[str, Any]:
        calls.append(url)
        return {}

    assert ga.refresh_if_stale(path, post=_post) == "fresh"
    assert not calls  # no network call when the token is still valid


def test_refresh_mints_new_token_when_stale(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_PAST))

    def _post(url: str, form: dict[str, str]) -> dict[str, Any]:
        assert url == "https://auth.x.ai/oauth2/token"
        assert form == {
            "grant_type": "refresh_token",
            "refresh_token": "rt",
            "client_id": _CLIENT,
        }
        return {
            "access_token": "new-access",
            "refresh_token": "new-rt",
            "expires_in": 21600,
        }

    assert ga.refresh_if_stale(path, post=_post) == "refreshed"
    creds = next(iter(json.loads(path.read_text()).values()))
    assert creds["key"] == "new-access"
    assert creds["refresh_token"] == "new-rt"  # rotated
    assert ga.is_valid(path)  # fresh expires_at ~6h out, valid against real now


def test_refresh_missing_file(tmp_path: Path) -> None:
    assert ga.refresh_if_stale(tmp_path / "nope.json") == "missing"


def test_refresh_no_refresh_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, {"https://auth.x.ai::c": {"key": "k"}})
    assert ga.refresh_if_stale(path) == "no_refresh_token"


def test_refresh_failed_on_post_error_leaves_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_PAST))

    def _boom(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        raise RuntimeError("network down")

    assert ga.refresh_if_stale(path, post=_boom) == "failed"
    creds = next(iter(json.loads(path.read_text()).values()))
    assert creds["key"] == "old-access"  # original credential preserved


def test_refresh_failed_when_no_access_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write(path, _bundle(_PAST))
    assert ga.refresh_if_stale(path, post=lambda _u, _f: {"expires_in": 1}) == "failed"


def test_refresh_persists_rotated_token_when_atomic_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F006: a rotated refresh_token is single-use — xAI invalidates the old one
    the moment it issues the new one. If the atomic write (tmp+replace) fails
    after the rotation, the file keeps the now-dead old refresh_token and the
    credential is permanently lost on the next refresh. The write must fall back
    to a direct write so the rotated refresh_token survives even when the atomic
    replace can't."""
    path = tmp_path / "auth.json"
    _write(path, _bundle(_PAST))

    def _post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {
            "access_token": "new-access",
            "refresh_token": "rotated-rt",
            "expires_in": 21600,
        }

    # Force the atomic tmp.replace to fail; the direct-write fallback must still
    # land the rotated refresh_token on disk.
    def _boom_replace(_self: pathlib.Path, _target: pathlib.Path) -> pathlib.Path:
        raise OSError("replace failed (simulated)")

    monkeypatch.setattr(pathlib.Path, "replace", _boom_replace)

    assert ga.refresh_if_stale(path, post=_post) == "refreshed"
    creds = next(iter(json.loads(path.read_text()).values()))
    assert creds["refresh_token"] == "rotated-rt"  # survived the write failure
    assert creds["key"] == "new-access"


def test_main_check_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".grok"
    home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(home))
    _write(home / "auth.json", _bundle(_FUTURE))
    assert ga.main(["--check"]) == 0
    _write(home / "auth.json", _bundle(_PAST))
    assert ga.main(["--check"]) == 1
