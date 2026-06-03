"""Authorization decisions for docs/optimal, expressed as gateway Envelopes.

The HTTP routes for documentation (`api.routes.docs`) and the knowledge
base (`api.routes.optimal`) used to embed RBAC checks inline and raise raw
``HTTPException(403)`` with no recovery hint. That mixed an authorization
decision into the HTTP layer and broke the gateway Envelope contract:
agents received ``remediate=null`` and had nothing actionable to do.

This module owns those decisions. It turns a denial into an
``Envelope.not_authorized(...)`` carrying a non-null ``remediate`` that
names the roles allowed to perform the action, so the agent knows how to
recover (escalate to a role that holds the permission). The routes stay
thin: they ask here for a verdict and translate it to the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.models.permissions import KB_PERMISSIONS
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from roboco.models.permissions import AgentContext
    from roboco.services.permissions import PermissionService


def _roles_allowed_for(action: str) -> list[str]:
    """Roles whose KB permission set includes ``action`` (sorted, stable)."""
    return sorted(
        role.value for role, actions in KB_PERMISSIONS.items() if action in actions
    )


def _remediate_for_action(action: str) -> str:
    """Recovery hint naming who can perform ``action``."""
    allowed = _roles_allowed_for(action)
    if allowed:
        return (
            f"role not permitted for '{action}' — ask one of these roles to "
            f"run it: {', '.join(allowed)}"
        )
    return f"'{action}' is not granted to any role; escalate to the CEO"


def authorize_kb_action(
    permissions: PermissionService,
    agent: AgentContext,
    action: str,
) -> Envelope | None:
    """Verdict on a knowledge-base action.

    Returns ``None`` when allowed. On denial returns an
    ``Envelope.not_authorized`` whose ``remediate`` tells the agent which
    roles may perform the action.
    """
    if permissions.can_perform_kb_action(agent, action):
        return None
    return Envelope.not_authorized(
        message=f"role '{agent.role.value}' not authorized to {action}",
        remediate=_remediate_for_action(action),
    )


_DOCS_WRITE_ACTIONS = frozenset({"write_doc", "delete_doc"})


def docs_denial_envelope(action: str, reason: str | None) -> Envelope:
    """Wrap a docs-service authorization denial as a gateway Envelope.

    The docs RBAC decision already lives in ``DocsService`` (it raises
    ``UnauthorizedError`` with an ``action`` and human ``reason``). This
    keeps the remediate-hint ownership in the gateway: the route hands the
    denial here and gets back the Envelope-shaped body with a non-null
    ``remediate``.
    """
    if action in _DOCS_WRITE_ACTIONS:
        remediate = (
            f"role not permitted to {action} — only documenters and cell PMs "
            "may write or delete docs; ask a documenter to perform it"
        )
    else:
        remediate = (
            f"role not permitted to {action} — ask a documenter or cell PM, "
            "or use roboco_kb_search to find the document instead"
        )
    return Envelope.not_authorized(
        message=reason or f"not authorized: {action}",
        remediate=remediate,
    )
