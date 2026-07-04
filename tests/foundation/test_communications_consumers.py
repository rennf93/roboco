"""Verify communications-policy consumers derive from foundation."""

from __future__ import annotations

import ast
from pathlib import Path

from roboco.foundation.policy import communications
from roboco.services.gateway import content_actions


def test_content_actions_notify_allowed_roles_matches_foundation() -> None:
    cfg_set = {
        r if isinstance(r, str) else r.value
        for r in content_actions._NOTIFY_ALLOWED_ROLES
    }
    foundation_set = {r.value for r in communications.NOTIFY_SENDER_ROLES}
    assert cfg_set == foundation_set, (
        f"_NOTIFY_ALLOWED_ROLES drift: cfg={cfg_set} foundation={foundation_set}"
    )


def test_content_actions_valid_priorities_matches_foundation() -> None:
    cfg = set(content_actions._VALID_NOTIFY_PRIORITIES)
    foundation = {p.value for p in communications.Priority}
    assert cfg == foundation


def test_notification_delivery_uses_ack_required_table() -> None:
    """All NotificationTable() construction sites must source `requires_ack`
    from ACK_REQUIRED_BY_TYPE, not from a hand-set boolean literal."""
    src = Path("roboco/services/notification_delivery.py").read_text()
    tree = ast.parse(src)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        callee_name = (
            callee.attr
            if isinstance(callee, ast.Attribute)
            else callee.id
            if isinstance(callee, ast.Name)
            else None
        )
        if callee_name != "NotificationTable":
            continue
        for kw in node.keywords:
            if kw.arg != "requires_ack":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                offenders.append(
                    f"line {kw.value.lineno}: requires_ack={kw.value.value}"
                )
    assert offenders == [], (
        "hand-set requires_ack literals remain in notification_delivery.py: "
        f"{offenders}"
    )
