"""Role-asserting dependencies and shared helpers for v2 flow routers.

Every router gets one of these as a dependency so the role check happens
before the choreographer body even runs. Defense in depth — the
choreographer also re-checks role internally for verbs that branch on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Depends, Header, HTTPException, params, status

if TYPE_CHECKING:
    from fastapi import Request

    from roboco.services.gateway.envelope import Envelope


def _require_roles(allowed: frozenset[str]) -> params.Depends:
    def _check(
        x_agent_role: Annotated[str, Header(alias="X-Agent-Role")],
    ) -> None:
        if x_agent_role.lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{x_agent_role}' not allowed for this endpoint group",
            )

    return cast("params.Depends", Depends(_check))


require_dev = _require_roles(frozenset({"developer"}))
require_qa = _require_roles(frozenset({"qa"}))
require_doc = _require_roles(frozenset({"documenter"}))
require_cell_pm = _require_roles(frozenset({"cell_pm"}))
require_main_pm = _require_roles(frozenset({"main_pm"}))
require_board = _require_roles(frozenset({"product_owner", "head_marketing"}))
require_auditor = _require_roles(frozenset({"auditor"}))


def envelope_to_response(env: Envelope, request: Request) -> dict[str, Any]:
    """Stamp the request's correlation_id onto the envelope and return wire-dict.

    ``CorrelationIdMiddleware`` writes the inbound (or freshly-generated)
    ``X-Correlation-ID`` to ``request.state.correlation_id``. We pull it
    here so the agent receives the same id it sent (or can capture the
    server-generated one) and ops can join logs across the full
    MCP -> API -> service hop.
    """
    cid = getattr(request.state, "correlation_id", None)
    if cid is not None and env.correlation_id is None:
        env.correlation_id = cid
    return env.as_dict()
