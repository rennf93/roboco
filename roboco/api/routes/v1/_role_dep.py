"""Role-asserting dependencies and shared helpers for v1 flow routers.

Every router gets one of these as a dependency so the role check happens
before the choreographer body even runs. Defense in depth — the
choreographer also re-checks role internally for verbs that branch on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Depends, Header, HTTPException, params, status

from roboco.foundation.identity import Role

if TYPE_CHECKING:
    from fastapi import Request

    from roboco.services.gateway.envelope import Envelope


def _require_roles(allowed: frozenset[Role]) -> params.Depends:
    def _check(
        x_agent_id: Annotated[str, Header(alias="X-Agent-ID")],
        x_agent_role: Annotated[str, Header(alias="X-Agent-Role")],
        x_agent_team: Annotated[str | None, Header(alias="X-Agent-Team")] = None,
        x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    ) -> None:
        # Bind the role header to a verified token BEFORE trusting it. These v1
        # flow guards are the sole gate for the /api/v1/flow/* endpoints, but
        # previously checked only the role string — unlike get_agent_context,
        # which already verifies the token. So a forged X-Agent-Role passed, and
        # in strict mode (ROBOCO_AGENT_AUTH_REQUIRED) the token was never
        # required here. In header-trust (dev) mode a missing token stays a
        # no-op; any presented token is still verified. Deferred import avoids
        # an import cycle with routers that import both this module and deps.
        from roboco.api.deps import _check_agent_auth_token

        _check_agent_auth_token(x_agent_id, x_agent_role, x_agent_team, x_agent_token)
        # `Role` is a StrEnum, so the lowercase header string compares equal
        # to its matching member.
        if x_agent_role.lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{x_agent_role}' not allowed for this endpoint group",
            )

    return cast("params.Depends", Depends(_check))


# Role-typed single-role guards — renaming a role edits foundation.identity only.
# `require_board` is the only multi-role guard (Product Owner + Head of Marketing
# share the public-facing board endpoints; the auditor has its own guard).
require_dev = _require_roles(frozenset({Role.DEVELOPER}))
require_qa = _require_roles(frozenset({Role.QA}))
require_doc = _require_roles(frozenset({Role.DOCUMENTER}))
require_cell_pm = _require_roles(frozenset({Role.CELL_PM}))
require_main_pm = _require_roles(frozenset({Role.MAIN_PM}))
require_board = _require_roles(frozenset({Role.PRODUCT_OWNER, Role.HEAD_MARKETING}))
require_auditor = _require_roles(frozenset({Role.AUDITOR}))
require_pr_reviewer = _require_roles(frozenset({Role.PR_REVIEWER}))


def _require_authenticated_agent() -> params.Depends:
    """Token-only guard for the content-tool (do) router (F003/F014).

    The do router serves every role — content tools are role-uniform, with
    per-role removal handled in the spawn manifest — so, unlike the flow
    routers, there is no single role to assert. But it must still bind the
    presented ``X-Agent-ID`` to a verified HMAC token when
    ``ROBOCO_AGENT_AUTH_REQUIRED=true`` and reject a forged token even in
    dev mode, exactly as the flow role guards do. Without this the
    ``/api/v1/do/*`` endpoints were the one agent-gateway path that
    accepted a forged ``X-Agent-ID`` with no token check — a weaker gate
    than ``/api/v1/flow/*``. The role/team headers are optional (the do
    MCP server sends role but not team); they only feed the HMAC payload,
    so a missing team is the empty-string team the token was issued with.
    """

    def _check(
        x_agent_id: Annotated[str, Header(alias="X-Agent-ID")],
        x_agent_role: Annotated[str | None, Header(alias="X-Agent-Role")] = None,
        x_agent_team: Annotated[str | None, Header(alias="X-Agent-Team")] = None,
        x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    ) -> None:
        from roboco.api.deps import _check_agent_auth_token

        _check_agent_auth_token(
            x_agent_id, x_agent_role or "", x_agent_team, x_agent_token
        )

    return cast("params.Depends", Depends(_check))


# The do router serves all roles, so this is token-only (no role assertion).
require_any_authenticated_agent = _require_authenticated_agent()


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
