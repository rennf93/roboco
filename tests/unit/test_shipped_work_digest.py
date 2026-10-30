"""Unit tests for the shared shipped-work digest helper.

Regression test: pins the helper's output format so the megaphone refactor
(moving ``digest_context``/``_shipped_this_week``/``_unreleased_changelog`` out
of ``MegaphoneEngine`` into ``roboco/utils/shipped_work_digest.py``) preserves
the exact output the megaphone exploration prompt rendered before.

Degradation test: when the digest cannot be assembled (no completed tasks,
missing/unreadable CHANGELOG) the helper degrades to explicit "unavailable"
lines rather than an empty string or a raised exception.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from roboco.models.base import Team
from roboco.utils.shipped_work_digest import shipped_work_digest

if TYPE_CHECKING:
    import pytest


def _mock_session(rows: list[tuple]) -> MagicMock:
    """A MagicMock async session whose ``execute`` returns ``rows`` from
    ``.all()`` — the only DB call ``_shipped_this_week`` makes."""
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def test_regression_digest_output_format_unchanged() -> None:
    """The helper's output matches the exact string ``MegaphoneEngine.
    digest_context`` produced before the refactor — the regression pin."""
    rows = [
        ("Add findings ledger", Team.BACKEND, "roboco-api"),
        ("Wire pr_pass gate", Team.BACKEND, "roboco-api"),
    ]
    session = _mock_session(rows)
    changelog_body = "### Added\n- New thing\n- Another thing\n"

    with patch(
        "roboco.utils.shipped_work_digest._unreleased_changelog",
        AsyncMock(return_value=changelog_body),
    ):
        got = await shipped_work_digest(session, "roboco-api")

    assert got == (
        "Completed this week:\n"
        "- Add findings ledger (roboco-api, backend)\n"
        "- Wire pr_pass gate (roboco-api, backend)\n"
        "\n"
        "CHANGELOG.md Unreleased section:\n"
        "### Added\n- New thing\n- Another thing\n"
    )


async def test_degradation_empty_shipped_and_missing_changelog() -> None:
    """No completed tasks + a changelog read failure degrades to explicit
    lines — never an empty string, never a raised exception."""
    session = _mock_session(rows=[])

    with patch(
        "roboco.utils.shipped_work_digest._unreleased_changelog",
        AsyncMock(return_value=""),
    ):
        got = await shipped_work_digest(session, "roboco-api")

    assert "Completed this week:" in got
    assert "- (nothing completed)" in got
    assert "CHANGELOG.md Unreleased section:" in got
    assert "(not available this cycle)" in got
    # The section is never empty — both halves carry an explicit line.
    assert got.strip() != ""


async def test_degradation_changelog_exception_does_not_raise() -> None:
    """A changelog read that raises internally is swallowed by
    ``_unreleased_changelog`` — the helper never propagates it."""
    session = _mock_session(rows=[])

    with patch(
        "roboco.services.workspace.get_workspace_service",
        side_effect=RuntimeError("clone blew up"),
    ):
        got = await shipped_work_digest(session, "roboco-api")

    assert "- (nothing completed)" in got
    assert "(not available this cycle)" in got


async def test_changelog_exception_emits_warning_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changelog read that raises internally emits a warning log before
    degrading to the empty string. The module-level structlog logger is
    patched with a Mock so the assertion is deterministic regardless of
    structlog's processor-chain / cache state at the point this test runs in
    the full suite (``capture_logs`` and ``caplog`` both depend on that
    state and fail when ``setup_logging`` reconfigures structlog after the
    logger's first-use cache)."""
    session = _mock_session(rows=[])
    fake_logger = MagicMock()
    _swd = importlib.import_module("roboco.utils.shipped_work_digest")
    monkeypatch.setattr(_swd, "logger", fake_logger)

    with patch(
        "roboco.services.workspace.get_workspace_service",
        side_effect=RuntimeError("clone blew up"),
    ):
        await shipped_work_digest(session, "roboco-api")

    fake_logger.warning.assert_called_once()
    assert "changelog" in fake_logger.warning.call_args.args[0].lower()
