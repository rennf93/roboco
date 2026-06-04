"""
Prompter Service

Provides a chat interface where an agent (or human via the API) can
converse with an LLM, with full message persistence, and can turn a
conversation into a real Task.

The LLM model is always resolved via ``ModelRoutingService`` — no
hardcoded model names appear here or in the route handlers.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.db.tables import (
    PrompterConversationTable,
    PrompterMessageTable,
)
from roboco.models.base import ModelProvider
from roboco.services.base import BaseService, NotFoundError, ServiceError
from roboco.services.llm import ModelRoutingService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_MAX_TITLE_LEN = 80


class PrompterService(BaseService):
    """Service for prompter chat conversations and task creation."""

    service_name: ClassVar[str] = "prompter"

    # =========================================================================
    # Conversation CRUD
    # =========================================================================

    async def create_conversation(self, title: str = "") -> PrompterConversationTable:
        """Create a new (empty) conversation."""
        conv = PrompterConversationTable(title=title)
        self.session.add(conv)
        await self.session.flush()
        self.log.info("Conversation created", conversation_id=str(conv.id))
        return conv

    async def get_conversation(
        self, conversation_id: UUID
    ) -> PrompterConversationTable:
        """Return a conversation with its messages eagerly loaded.

        Raises ``NotFoundError`` if the id does not exist.
        """
        result = await self.session.execute(
            select(PrompterConversationTable)
            .where(PrompterConversationTable.id == conversation_id)
            .options(selectinload(PrompterConversationTable.messages))
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise NotFoundError(
                resource_type="PrompterConversation",
                resource_id=str(conversation_id),
            )
        return conv

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PrompterConversationTable]:
        """Return conversations ordered by most-recently-updated first."""
        result = await self.session.execute(
            select(PrompterConversationTable)
            .order_by(PrompterConversationTable.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def delete_conversation(self, conversation_id: UUID) -> None:
        """Delete a conversation (cascade removes messages).

        Raises ``NotFoundError`` if the id does not exist.
        """
        result = await self.session.execute(
            select(PrompterConversationTable).where(
                PrompterConversationTable.id == conversation_id
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise NotFoundError(
                resource_type="PrompterConversation",
                resource_id=str(conversation_id),
            )
        await self.session.delete(conv)
        await self.session.flush()
        self.log.info("Conversation deleted", conversation_id=str(conversation_id))

    # =========================================================================
    # Chat
    # =========================================================================

    async def chat(
        self,
        user_message: str,
        agent_slug: str,
        conversation_id: UUID | None = None,
    ) -> tuple[PrompterConversationTable, str, str]:
        """Send a user message and get an LLM response.

        If ``conversation_id`` is ``None`` a new conversation is created
        automatically.  The user message and assistant response are both
        persisted as ``PrompterMessage`` rows.

        Returns:
            ``(conversation, assistant_text, model_used)`` where
            ``model_used`` is the model name resolved via
            ``ModelRoutingService``.
        """
        # --- fetch or create conversation ---
        if conversation_id is not None:
            conv = await self.get_conversation(conversation_id)
        else:
            title = (
                (user_message[:_MAX_TITLE_LEN] + "…")
                if len(user_message) > _MAX_TITLE_LEN
                else user_message
            )
            conv = await self.create_conversation(title=title)

        # --- load messages for context window ---
        result = await self.session.execute(
            select(PrompterMessageTable)
            .where(PrompterMessageTable.conversation_id == conv.id)
            .order_by(PrompterMessageTable.created_at)
        )
        history: list[PrompterMessageTable] = list(result.scalars().all())

        # --- persist user message ---
        user_msg = PrompterMessageTable(
            conversation_id=conv.id,  # type: ignore[arg-type]
            role="user",
            content=user_message,
        )
        self.session.add(user_msg)
        await self.session.flush()

        # --- build context for LLM ---
        lm_messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in history
        ]
        lm_messages.append({"role": "user", "content": user_message})

        # --- resolve model + call LLM ---
        assistant_text, model_used = await self._call_llm(lm_messages, agent_slug)

        # --- persist assistant message ---
        assistant_msg = PrompterMessageTable(
            conversation_id=conv.id,  # type: ignore[arg-type]
            role="assistant",
            content=assistant_text,
            model_used=model_used,
        )
        self.session.add(assistant_msg)

        # --- update conversation stats ---
        conv.message_count = len(history) + 2  # user + assistant
        await self.session.flush()

        self.log.info(
            "Chat turn completed",
            conversation_id=str(conv.id),
            model_used=model_used,
        )
        return conv, assistant_text, model_used

    # =========================================================================
    # Task Creation from Conversation
    # =========================================================================

    async def create_task_from_conversation(
        self,
        conversation_id: UUID,
        agent_slug: str,
        created_by: UUID,
        project_id: UUID | None = None,
        product_id: UUID | None = None,
    ) -> UUID:
        """Generate a task draft from a conversation and create a real Task.

        Reads the conversation history, asks the LLM to produce a structured
        task draft (JSON), validates it, and creates a ``TaskTable`` row via
        ``TaskService``.

        Returns the new task's UUID.

        Raises:
            ``NotFoundError`` — conversation does not exist.
            ``ServiceError``  — LLM returned unparseable JSON or the draft
                                fails ``TaskCreate`` validation.
        """
        from roboco.models.base import Complexity, TaskNature, TaskType
        from roboco.models.task import TaskCreate, TaskCreateRequest
        from roboco.services.task import get_task_service

        if project_id is None and product_id is None:
            raise ServiceError(
                "Either project_id or product_id must be provided to create a task"
            )

        conv = await self.get_conversation(conversation_id)
        result = await self.session.execute(
            select(PrompterMessageTable)
            .where(PrompterMessageTable.conversation_id == conv.id)
            .order_by(PrompterMessageTable.created_at)
        )
        history: list[PrompterMessageTable] = list(result.scalars().all())

        conversation_text = "\n".join(f"{m.role.upper()}: {m.content}" for m in history)

        from roboco.foundation.identity import Team

        valid_teams = [t.value for t in Team]
        valid_types = [t.value for t in TaskType]
        valid_natures = [n.value for n in TaskNature]
        valid_complexities = [c.value for c in Complexity]

        task_prompt = (
            "Based on the following conversation, generate a task draft as JSON.\n\n"
            f"Conversation:\n{conversation_text}\n\n"
            "Return ONLY a JSON object (no markdown, no code fences) with these"
            " fields:\n"
            "  title: string (1-200 chars)\n"
            "  description: string (at least 20 chars)\n"
            "  acceptance_criteria: array of strings (at least 1 item)\n"
            f"  team: one of {valid_teams}\n"
            f"  task_type: one of {valid_types}\n"
            f"  nature: one of {valid_natures}\n"
            f"  estimated_complexity: one of {valid_complexities}\n\n"
            "Example:\n"
            '{"title":"Implement login","description":"Add JWT auth to the API",'
            '"acceptance_criteria":["POST /login returns token"],'
            '"team":"backend","task_type":"code","nature":"technical",'
            '"estimated_complexity":"medium"}'
        )

        lm_messages: list[dict[str, str]] = [{"role": "user", "content": task_prompt}]
        draft_json, _ = await self._call_llm(lm_messages, agent_slug)

        # --- parse draft ---
        draft = self._parse_task_draft(draft_json)

        # --- validate via TaskCreate Pydantic model ---
        try:
            task_create = TaskCreate(
                title=draft.get("title", ""),
                description=draft.get("description", ""),
                acceptance_criteria=draft.get("acceptance_criteria", []),
                team=draft.get("team", ""),
                task_type=draft.get("task_type", ""),
                nature=draft.get("nature", ""),
                estimated_complexity=draft.get("estimated_complexity", ""),
                project_id=project_id,
                product_id=product_id,
            )
        except Exception as exc:
            raise ServiceError(
                f"LLM-generated task draft failed validation: {exc}",
                details={"draft": draft},
            ) from exc

        # --- create the task ---
        task_svc = get_task_service(self.session)
        req = TaskCreateRequest(
            title=task_create.title,
            description=task_create.description,
            acceptance_criteria=task_create.acceptance_criteria,
            team=task_create.team,
            created_by=created_by,
            task_type=task_create.task_type,
            nature=task_create.nature,
            estimated_complexity=task_create.estimated_complexity,
            project_id=task_create.project_id,
            product_id=task_create.product_id,
        )
        task = await task_svc.create(req)
        await self.session.flush()

        self.log.info(
            "Task created from conversation",
            conversation_id=str(conversation_id),
            task_id=str(task.id),
        )
        return UUID(str(task.id))

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _call_llm(
        self,
        messages: list[dict[str, str]],
        agent_slug: str,
    ) -> tuple[str, str]:
        """Resolve route via ModelRoutingService and call the LLM.

        Returns ``(response_text, model_name)``.
        """
        routing_svc = ModelRoutingService(self.session)
        route = await routing_svc.resolve_for_agent(agent_slug)

        if route.provider_type == ModelProvider.ANTHROPIC:
            return await self._call_anthropic(messages, route)
        # All other providers (OLLAMA_CLOUD, OPENAI, LOCAL) use the
        # OpenAI-compatible chat completions endpoint.
        return await self._call_openai_compat(messages, route)

    async def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        route: Any,
    ) -> tuple[str, str]:
        """Call Anthropic API and return (text, model_name)."""
        from anthropic import AsyncAnthropic

        from roboco.config import settings

        api_key = route.auth_token or settings.anthropic_api_key
        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=route.model_name,
            max_tokens=4096,
            messages=messages,  # type: ignore[arg-type]
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text  # type: ignore[union-attr]
                break
        return text, route.model_name

    async def _call_openai_compat(
        self,
        messages: list[dict[str, str]],
        route: Any,
    ) -> tuple[str, str]:
        """Call an OpenAI-compatible endpoint (Ollama, etc.).

        Uses ``route.base_url`` when set, otherwise falls back to
        ``settings.local_llm_base_url``.
        """
        import httpx

        from roboco.config import settings

        base_url = route.base_url or settings.local_llm_base_url
        headers: dict[str, str] = {}
        if route.auth_token:
            headers["Authorization"] = f"Bearer {route.auth_token}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": route.model_name,
                    "messages": messages,
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        text: str = data["choices"][0]["message"]["content"]
        return text, route.model_name

    @staticmethod
    def _parse_task_draft(raw: str) -> dict[str, Any]:
        """Extract a JSON object from the LLM's raw response.

        Handles both bare JSON and JSON wrapped in markdown code fences.
        Raises ``ServiceError`` when no valid JSON object can be extracted.
        """
        # Try stripping markdown code fences first
        clean = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", clean)
        if fence_match:
            clean = fence_match.group(1).strip()

        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return data
            raise ServiceError(
                "LLM task draft was JSON but not an object", details={"raw": raw}
            )
        except json.JSONDecodeError as json_err:
            # Try extracting first JSON object from surrounding text
            obj_match = re.search(r"\{[\s\S]+\}", clean)
            if obj_match:
                try:
                    data = json.loads(obj_match.group(0))
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    pass
            raise ServiceError(
                "Could not parse task draft from LLM response as JSON",
                details={"raw": raw[:500]},
            ) from json_err


def get_prompter_service(session: AsyncSession) -> PrompterService:
    """Factory function for PrompterService."""
    return PrompterService(session)
