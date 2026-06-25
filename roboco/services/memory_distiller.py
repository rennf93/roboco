"""MemoryDistiller — distill a high-signal completion lesson via the local LLM.

Replaces the noisy raw-notes / duration / commit-count capture with one curated
lesson in a fixed Problem -> Approach -> Gotcha shape (<=120 words). Runs on the
LOCAL model only (glm-5:cloud via the OpenAI-compatible endpoint) — never a cloud
LLM in the hot path. Best-effort: any failure (LLM down, empty output) returns
None and the caller records nothing rather than storing junk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog

from roboco.config import settings

logger = structlog.get_logger()

_MAX_WORDS = 120
_CHAT_TIMEOUT_SECONDS = 60.0


@dataclass
class LessonInput:
    """The completed-task facts the distiller turns into one reusable lesson."""

    title: str
    acceptance_criteria: list[str] = field(default_factory=list)
    dev_notes: str | None = None
    qa_notes: str | None = None
    commit_messages: list[str] = field(default_factory=list)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- (none)"


def _build_prompt(snapshot: LessonInput) -> str:
    criteria = _bullets(snapshot.acceptance_criteria)
    commits = _bullets(snapshot.commit_messages)
    dev = (snapshot.dev_notes or "(none)").strip()
    qa = (snapshot.qa_notes or "(none)").strip()
    return (
        "You are distilling one reusable engineering lesson from a completed "
        "task, for future agents. Write at most 120 words, in exactly this "
        "shape:\n"
        "Problem: <what was hard>\n"
        "Approach: <what worked>\n"
        "Gotcha: <the trap to avoid next time>\n\n"
        "Be concrete and specific; no fluff, no restating the task. If there is "
        "no real lesson, reply with exactly NONE.\n\n"
        f"Task: {snapshot.title}\n"
        f"Acceptance criteria:\n{criteria}\n"
        f"Developer notes:\n{dev}\n"
        f"QA notes:\n{qa}\n"
        f"Commit messages:\n{commits}\n"
    )


async def _chat(prompt: str) -> str | None:
    """One local-LLM chat call (OpenAI-compatible); None on a non-success."""
    async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{settings.local_llm_base_url}/chat/completions",
            json={
                "model": settings.local_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "options": {"num_ctx": 8192},
            },
        )
        if not resp.is_success:
            return None
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content")
        return content if isinstance(content, str) else None


class MemoryDistiller:
    """Turn a completed task into one curated lesson (best-effort, local LLM)."""

    async def distill(self, snapshot: LessonInput) -> str | None:
        """Return a <=120-word lesson, or None on failure / no real lesson."""
        try:
            result = await _chat(_build_prompt(snapshot))
        except Exception as exc:
            logger.warning("MemoryDistiller failed (best-effort)", error=str(exc))
            return None
        lesson = (result or "").strip()
        if not lesson or lesson.upper() == "NONE":
            return None
        words = lesson.split()
        return " ".join(words[:_MAX_WORDS]) if len(words) > _MAX_WORDS else lesson
