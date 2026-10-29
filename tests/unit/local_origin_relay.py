"""Serve a test's local bare ``origin`` through a real relay.

Real-git tests build a bare repository as ``origin`` but mock the database.
``GitService`` serves every ``origin`` command through the project's relay,
which normally comes from the project row; this points it at the test's bare
repository (``file://``) instead, so fetch / push / ls-remote still run the
real relay round-trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.services.git import GitService
from roboco.services.git_relay import GitRelay

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def serve_origin_through_relay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, origin: Path
) -> GitRelay:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    relay = GitRelay("test-project", f"file://{origin}", None)

    async def _relay_for_repo(_self: GitService, _repo: Path) -> GitRelay:
        return relay

    monkeypatch.setattr(GitService, "_relay_for_repo", _relay_for_repo)
    return relay
