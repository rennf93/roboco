"""Guard: no pre-gateway tool names may appear in live ``roboco/`` sources.

The Gateway/full cutover deleted the v1 per-domain MCP tools. Any surviving
reference in a spawn prompt, seed, onboarding string, or comment hands agents
(or future readers) a tool that no longer exists. This test fails if any
reappear.

The orphaned ``roboco/agents/`` subtree is excluded — it is pre-gateway dead
code removed wholesale in a later phase, so there is no value in scrubbing its
strings first.
"""

from __future__ import annotations

import pathlib

# Deleted v1 tool names (the gateway replaced them with bare verbs like
# give_me_work / i_am_done / triage / notify / note). roboco_ask_mentor and
# roboco_kb_search are intentionally absent — those are still live.
FORBIDDEN: tuple[str, ...] = (
    "roboco_task_scan",
    "roboco_task_claim",
    "roboco_task_get",
    "roboco_task_complete",
    "roboco_task_escalate",
    "roboco_task_escalate_to_ceo",
    "roboco_task_substitute",
    "roboco_task_submit_qa",
    "roboco_task_submit_verification",
    "roboco_task_submit_pm_review",
    "roboco_task_qa_pass",
    "roboco_task_qa_fail",
    "roboco_task_docs_complete",
    "roboco_task_create",
    "roboco_task_activate",
    "roboco_task_cancel",
    "roboco_task_plan",
    "roboco_task_start",
    "roboco_task_progress",
    "roboco_task_pause",
    "roboco_task_block",
    "roboco_task_unblock",
    "roboco_agent_idle",
    "roboco_escalate",
    "roboco_notify_ack",
    "roboco_notify_send",
    "roboco_notify_list",
    "roboco_notify_get",
    "roboco_message_send",
    "roboco_session_create_for_tasks",
    "roboco_journal_decision",
    "roboco_journal_learning",
    "roboco_journal_struggle",
    "roboco_journal_reflect",
    "roboco_journal_entry",
)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PKG = _ROOT / "roboco"
# Pre-gateway agent subtree, removed wholesale in a later phase.
_EXCLUDED_DIR = _PKG / "agents"


def _excluded(path: pathlib.Path) -> bool:
    return _EXCLUDED_DIR in path.parents


def test_no_deleted_tool_names_in_runtime_sources() -> None:
    hits: list[str] = []
    for path in _PKG.rglob("*.py"):
        if _excluded(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in FORBIDDEN:
            if name in text:
                hits.append(f"{path.relative_to(_ROOT)} :: {name}")
    assert not hits, "Deleted v1 tool names still referenced:\n" + "\n".join(hits)
