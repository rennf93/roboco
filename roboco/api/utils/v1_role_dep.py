"""Side-effect-free helpers backing roboco/api/routes/v1/_role_dep.py.

Role-asserting dependency factories, the token-only auth guard, and the
envelope-to-wire-response + rejection-logging helpers used by every v1 flow
router. Kept here (not in routes/v1/_role_dep.py) so the routes module stays
thin per the architectural map; ``_role_dep.py`` re-imports these and builds
the module-level per-role guard instances from them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

import structlog
from fastapi import Depends, Header, HTTPException, params, status

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from fastapi import Request

    from roboco.foundation.identity import Role
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


def _require_authenticated_agent() -> params.Depends:
    """Token-only guard for the content-tool (do) router.

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
    payload = env.as_dict()
    _log_rejection(payload, request)
    return payload


def _log_rejection(payload: dict[str, Any], request: Request) -> None:
    """Log a rejected envelope's reason at the single wire chokepoint.

    Rejections used to leave NO server-side trace: the access log records
    ``POST /api/v1/do/<verb> 200`` (an error envelope is still a 200), the
    envelope body is never logged, and there is no trace table — so a verb
    an agent could not satisfy was indistinguishable in the logs from one
    that succeeded. Four Board Programs died that way on 2026-07-25 and the
    reason was unrecoverable after the fact.
    """
    error = payload.get("error")
    if not error:
        return
    logger.warning(
        "verb rejected",
        verb=request.url.path.rsplit("/", 1)[-1],
        error=error,
        detail=payload.get("message"),
        remediate=payload.get("remediate"),
        missing=payload.get("missing"),
        agent_id=request.headers.get("X-Agent-ID"),
        agent_role=request.headers.get("X-Agent-Role"),
        task_id=payload.get("task_id"),
    )
