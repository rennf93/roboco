"""Refreshed provider credentials are handed to the agent uid, mode 0600."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.llm.providers import _credential_file as cf

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# auth.json must stay readable by its owner (the agent uid) alone.
_OWNER_ONLY_MODE = 0o600


def test_root_hands_the_file_to_the_agent_uid_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent containers (uid 1000) bind-mount auth.json read-only: a root-owned
    0600 file would lock them out, so root must chown it to the agent uid."""
    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(cf.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        cf.os, "fchown", lambda _fd, uid, gid: chowned.append((uid, gid))
    )
    path = tmp_path / "auth.json"

    cf.write_agent_credential(path, '{"k": "v"}')

    assert chowned == [(cf.AGENT_UID, -1)]
    assert path.read_text() == '{"k": "v"}'
    assert path.stat().st_mode & 0o777 == _OWNER_ONLY_MODE


def test_unprivileged_writer_keeps_its_own_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[object] = []
    monkeypatch.setattr(cf.os, "fchown", lambda *a: called.append(a))
    path = tmp_path / "auth.json"
    path.write_text("old")
    path.chmod(0o664)

    cf.write_agent_credential(path, "new")

    assert called == []
    assert path.read_text() == "new"
    assert path.stat().st_mode & 0o777 == _OWNER_ONLY_MODE
