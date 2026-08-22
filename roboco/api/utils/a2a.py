"""
A2A Route Helpers

Route-glue helpers backing roboco/api/routes/a2a.py.
"""

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.models.a2a import A2AConversation


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or reply to the A2A live view")


def _resolve_reply_target(conv: A2AConversation, to_agent: str) -> None:
    """Validate the CEO's reply target against the pairwise conversation.

    Raises the appropriate 400 HTTPException — kept out of the route handler
    to keep its cyclomatic complexity low. A2A conversations are strictly
    pairwise (no N-party thread), so the CEO must address one of the two
    real participants; A2A is also scoped to a task by construction
    (A2AService.send requires task_id), so an untethered conversation can't
    be replied into via this path.
    """
    if to_agent not in (conv.agent_a, conv.agent_b):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{to_agent} is not a participant in this conversation "
                f"(participants: {conv.agent_a}, {conv.agent_b})"
            ),
        )
    if conv.task_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conversation has no linked task_id — A2A requires one",
        )
