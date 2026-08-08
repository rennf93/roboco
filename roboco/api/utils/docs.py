"""
Docs Route Helpers

Route-glue helpers backing roboco/api/routes/docs.py.
"""

from fastapi import status
from fastapi.responses import JSONResponse

from roboco.services.base import UnauthorizedError
from roboco.services.gateway.kb_authz import docs_denial_envelope


def unauthorized_response(err: UnauthorizedError) -> JSONResponse:
    """Render a docs-service denial as the gateway Envelope (HTTP 403).

    The RBAC decision is made in ``DocsService`` (it raises
    ``UnauthorizedError``); this only renders that denial at the HTTP
    boundary. The body is the Envelope wire-dict at top level so the agent
    receives a non-null ``remediate`` instead of a bare ``detail`` string.
    """
    envelope = docs_denial_envelope(err.action, err.reason)
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=envelope.as_dict(),
    )
