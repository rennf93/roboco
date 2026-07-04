"""propose_video is granted to every developer (Role.DEVELOPER doesn't
distinguish ux-dev from be-dev/fe-dev) — the REAL gate is the runtime
_caller_team check in ContentActions.propose_video, covered separately in
test_content_actions_video.py."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_developer_gets_propose_video() -> None:
    assert "propose_video" in get_role_config("developer").do_tools


def test_qa_does_not_get_propose_video() -> None:
    assert "propose_video" not in get_role_config("qa").do_tools


def test_documenter_does_not_get_propose_video() -> None:
    assert "propose_video" not in get_role_config("documenter").do_tools


def test_head_marketing_does_not_get_propose_video() -> None:
    assert "propose_video" not in get_role_config("head_marketing").do_tools
