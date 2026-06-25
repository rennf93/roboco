"""Schemas for the Auditor playbook-curation surface."""

from pydantic import BaseModel, Field


class PlaybookRejectBody(BaseModel):
    """The Auditor's reason when rejecting (archiving) a playbook."""

    reason: str = Field(..., min_length=1)
