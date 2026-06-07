"""
Prompter Service

Conversational LLM assistant that helps users draft tasks.
Uses Anthropic Claude for natural-language interaction and
structured JSON draft generation.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import structlog
from anthropic import AsyncAnthropic

from roboco.config import settings
from roboco.services.base import ServiceError, ValidationError

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROMPTER_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter — a conversational assistant that helps "
    "users draft tasks for an AI agentic company.\n\n"
    "Your job is to:\n"
    "1. Ask clarifying questions to gather requirements.\n"
    "2. Keep the conversation focused on producing a well-scoped task.\n"
    "3. When you believe you have enough context, signal that a draft is "
    "ready.\n"
    "4. Never create the task yourself — only help the user articulate what "
    "needs to be built.\n\n"
    "Key rules:\n"
    "- Be concise but thorough.\n"
    "- Always ask for acceptance criteria if the user hasn't provided them.\n"
    "- Suggest a team (backend, frontend, ux_ui) based on the work "
    "described.\n"
    "- Estimate complexity (low, medium, high) and task type (code, "
    "documentation, research, planning, design, administrative).\n"
    "- Determine nature (technical vs non_technical).\n"
    "- If the user describes a bug, suggest a code task with technical "
    "nature.\n"
    "- If the user describes a feature, determine whether it's backend, "
    "frontend, or UX/UI work.\n\n"
    "When you have enough information to produce a complete draft, set "
    "draft_ready=true in your reasoning."
)

_DRAFT_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter — an expert at converting conversations "
    "into structured task drafts.\n\n"
    "Given a conversation between a user and the Prompter assistant, "
    "produce a JSON task draft that conforms to the RoboCo task schema.\n\n"
    "Required fields:\n"
    "- title: concise, actionable task title (max 200 chars)\n"
    "- description: detailed description, min 20 chars, explaining what "
    "needs to be done\n"
    "- acceptance_criteria: list of strings, each a verifiable criterion "
    "(min 1)\n"
    "- team: one of backend, frontend, ux_ui\n"
    "- task_type: one of code, documentation, research, planning, design, "
    "administrative\n"
    "- nature: one of technical, non_technical\n"
    "- estimated_complexity: one of low, medium, high\n"
    "- priority: integer 0-3 (0=P0 highest, 3=P3 lowest)\n\n"
    "Optional fields:\n"
    "- project_id: UUID string if known from context\n"
    "- product_id: UUID string if known from context (only one of "
    "project_id/product_id should be set)\n"
    "- assigned_to: agent slug or UUID if the user specified one\n"
    "- target_date: ISO-8601 date string if mentioned\n\n"
    'Always set source="prompter" and confirmed_by_human=false.\n\n'
    "Return ONLY valid JSON matching the PrompterDraftTask schema. No "
    "markdown, no preamble."
)


class _PrompterServiceHolder:
    """Lazy singleton holder for PrompterService."""

    _instance: PrompterService | None = None

    @classmethod
    def get(cls) -> PrompterService:
        if cls._instance is None:
            cls._instance = PrompterService()
        return cls._instance


class PrompterService:
    """Service for Prompter chat and structured draft generation."""

    service_name: ClassVar[str] = "prompter"

    def __init__(self) -> None:
        self.log = logger.bind(component="prompter_service")
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        """Lazy-init Anthropic client."""
        if self._client is None:
            api_key = settings.anthropic_api_key
            if not api_key:
                raise ServiceError("Anthropic API key not configured")
            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    # -----------------------------------------------------------------------
    # Chat
    # -----------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """
        Continue a Prompter conversation.

        Args:
            messages: Conversation history as {role, content} dicts.
            context: Optional context dict (project_id, team, etc.).
            model: Anthropic model identifier.
            max_tokens: Maximum tokens in the response.

        Returns:
            dict with keys: message (str), draft_ready (bool).

        Raises:
            ServiceError: If the LLM call fails or returns malformed output.
        """
        client = self._get_client()

        # Build the user prompt with context if provided
        user_prompt = self._build_chat_prompt(messages, context)

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_PROMPTER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            self.log.error("Prompter chat LLM call failed", error=str(e))
            raise ServiceError(f"LLM chat failed: {e}") from e

        # Extract text from response blocks
        content = self._extract_text(response)
        if not content:
            raise ServiceError("LLM returned empty content")

        # Heuristic: detect draft-ready signal in the response
        draft_ready = self._detect_draft_ready(content)

        return {
            "message": content,
            "draft_ready": draft_ready,
        }

    # -----------------------------------------------------------------------
    # Draft
    # -----------------------------------------------------------------------

    async def draft(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """
        Generate a structured task draft from conversation context.

        Args:
            messages: Full conversation used as drafting context.
            context: Optional overrides (project_id, team, etc.).
            model: Anthropic model identifier.
            max_tokens: Maximum tokens in the response.

        Returns:
            dict with keys: draft (dict), reasoning (str).

        Raises:
            ValidationError: If the LLM output does not conform to the schema.
            ServiceError: If the LLM call fails.
        """
        client = self._get_client()

        # Serialize conversation into a single prompt
        user_prompt = self._build_draft_prompt(messages, context)

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_DRAFT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            self.log.error("Prompter draft LLM call failed", error=str(e))
            raise ServiceError(f"LLM draft generation failed: {e}") from e

        content = self._extract_text(response)
        if not content:
            raise ServiceError("LLM returned empty content for draft")

        # Parse JSON from the response
        try:
            draft_data = json.loads(content)
        except json.JSONDecodeError as e:
            self.log.warning(
                "Draft JSON parse failed", content_preview=content[:200]
            )
            raise ValidationError(
                message=f"Draft response was not valid JSON: {e}",
                field="draft",
            ) from e

        # Ensure provenance fields are set correctly regardless of LLM output
        draft_data["source"] = "prompter"
        draft_data["confirmed_by_human"] = False

        # Derive a short reasoning string
        reasoning = self._build_reasoning(messages, draft_data)

        return {
            "draft": draft_data,
            "reasoning": reasoning,
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_chat_prompt(
        messages: list[dict[str, str]],
        context: dict[str, Any] | None,
    ) -> str:
        """Serialize messages + context into a single user prompt."""
        lines: list[str] = []
        if context:
            lines.append("Context:")
            for key, value in context.items():
                lines.append(f"  {key}: {value}")
            lines.append("")
        lines.append("Conversation:")
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        lines.append("")
        lines.append(
            "Continue the conversation as the Prompter assistant. "
            "If you have enough information to draft a complete task, "
            "say so explicitly and set draft_ready=true in your reasoning."
        )
        return "\n".join(lines)

    @staticmethod
    def _build_draft_prompt(
        messages: list[dict[str, str]],
        context: dict[str, Any] | None,
    ) -> str:
        """Serialize conversation into a draft-generation prompt."""
        lines: list[str] = []
        lines.append(
            "Produce a JSON task draft from the following conversation. "
            "Return ONLY valid JSON — no markdown, no preamble."
        )
        if context:
            lines.append("")
            lines.append("Overrides:")
            for key, value in context.items():
                lines.append(f"  {key}: {value}")
        lines.append("")
        lines.append("Conversation:")
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Extract text from Anthropic message response."""
        text_parts: list[str] = []
        for block in getattr(response, "content", []):
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts).strip()

    @staticmethod
    def _detect_draft_ready(content: str) -> bool:
        """Heuristic: assistant signals readiness with explicit phrases."""
        signals = [
            "i have enough information",
            "ready to generate a draft",
            "ready to draft",
            "i can now draft",
            "draft_ready=true",
            "draft ready",
        ]
        lower = content.lower()
        return any(sig in lower for sig in signals)

    @staticmethod
    def _build_reasoning(
        messages: list[dict[str, str]],
        draft_data: dict[str, Any],
    ) -> str:
        """Derive a short reasoning summary from the conversation and draft."""
        title = draft_data.get("title", "Untitled")
        team = draft_data.get("team", "unknown")
        complexity = draft_data.get("estimated_complexity", "unknown")
        return (
            f"Draft generated from conversation of {len(messages)} messages. "
            f"Proposed task '{title}' for team {team} "
            f"with complexity {complexity}."
        )


def get_prompter_service() -> PrompterService:
    """Get or create the PrompterService singleton."""
    return _PrompterServiceHolder.get()
