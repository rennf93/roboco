"""Release certificate API — the exportable proof a release was governed.

CEO-only. ``GET /{version}/certificate`` packages one published release's
full gate chain (CI verdict, conventions cleanliness, per-AC QA states,
findings summary, CEO approval timestamp, changelog excerpt) into a single
artifact for the panel's "Download certificate" action. Read-only — no
approval, publish, or state change passes through here.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.release import ReleaseCertificateResponse
from roboco.api.utils.release import _require_ceo
from roboco.services.release_certificate import ReleaseCertificateService

router = APIRouter()


@router.get("/{version}/certificate", response_model=ReleaseCertificateResponse)
async def get_release_certificate(
    version: str, db: DbSession, agent: CurrentAgentContext
) -> ReleaseCertificateResponse:
    """The governance certificate for one published (v-prefixed ok) release."""
    _require_ceo(agent)
    certificate = await ReleaseCertificateService(db).build_certificate(version)
    if certificate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No published release for version {version!r}",
        )
    return ReleaseCertificateResponse(**asdict(certificate))
