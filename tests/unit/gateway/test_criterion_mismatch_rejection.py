"""The finding-criterion rejection names every acceptance criterion by id AND
text, so a reviewer can resend the right id instead of another paraphrase.

Live 2026-10-28: QA ``fail``, ``pr_fail`` and PM ``request_changes`` were
rejected 12 times in under two hours with only opaque ids to choose from.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.services.gateway.choreographer import findings as findings_lib


def test_lists_each_criterion_id_next_to_its_text() -> None:
    task = SimpleNamespace(
        acceptance_criteria_ids=["aaa111", "bbb222"],
        acceptance_criteria=["The panel shows a Stuck filter", "make gate passes"],
    )

    env = findings_lib.criterion_mismatch_rejection(task, ["Stuck filter ..."])

    assert env.error == "invalid_state"
    message = env.message or ""
    assert "['Stuck filter ...']" in message
    assert "aaa111 = The panel shows a Stuck filter" in message
    assert "bbb222 = make gate passes" in message
    assert "one of the ids above" in (env.remediate or "")


def test_clips_a_long_criterion_text() -> None:
    long_text = "x" * 500
    task = SimpleNamespace(
        acceptance_criteria_ids=["ccc333"], acceptance_criteria=[long_text]
    )

    env = findings_lib.criterion_mismatch_rejection(task, ["nope"])

    message = env.message or ""
    assert long_text not in message
    assert "ccc333 = " + "x" * 139 + "…" in message


def test_lists_bare_texts_for_a_task_without_criterion_ids() -> None:
    task = SimpleNamespace(acceptance_criteria=["Legacy criterion"])

    env = findings_lib.criterion_mismatch_rejection(task, ["nope"])

    assert "(id = text): Legacy criterion" in (env.message or "")
