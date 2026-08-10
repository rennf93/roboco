"""
Optimal (KB/RAG) Route Helpers

Route-glue helpers backing roboco/api/routes/optimal.py.
"""

from fastapi import status
from fastapi.responses import JSONResponse

from roboco.api.deps import CurrentAgentContext, PermissionServiceDep
from roboco.services.gateway.kb_authz import authorize_kb_action


def kb_denial_response(
    permissions: PermissionServiceDep,
    agent: CurrentAgentContext,
    action: str,
) -> JSONResponse | None:
    """Gateway Envelope (HTTP 403) when the KB action is denied, else None.

    The authorization decision itself lives in the gateway
    (``authorize_kb_action``); this only renders a denial verdict at the HTTP
    boundary. The body is the Envelope wire-dict at top level — not nested
    under ``detail`` — so the agent receives a non-null ``remediate`` it can
    act on, matching the gateway Envelope contract.
    """
    denial = authorize_kb_action(permissions, agent, action)
    if denial is None:
        return None
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=denial.as_dict(),
    )
