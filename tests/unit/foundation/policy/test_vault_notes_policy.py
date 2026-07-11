"""Shared vault-note content hash: stable across either engine's own
feedback-callout convention, so appending one never re-triggers the other's
change-detection scan."""

from __future__ import annotations

from roboco.foundation.policy.vault_notes import content_hash

_BODY = "# Buy milk\n\nGet 2% milk.\n"


def test_hash_stable_across_intake_drafted_callout() -> None:
    with_callout = (
        _BODY
        + "\n> [!info] RoboCo: drafted Vault note: buy milk (abcd1234) on 2026-07-11\n"
    )
    assert content_hash(_BODY) == content_hash(with_callout)


def test_hash_stable_across_kb_quarantine_callout() -> None:
    with_callout = (
        _BODY + "\n> [!warning] RoboCo: quarantined (injection pattern detected) "
        "on 2026-07-11\n"
    )
    assert content_hash(_BODY) == content_hash(with_callout)


def test_hash_differs_on_real_content_change() -> None:
    assert content_hash(_BODY) != content_hash(_BODY + "\nAlso get bread.\n")
