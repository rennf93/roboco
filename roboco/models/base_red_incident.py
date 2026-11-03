"""Base-red incident read model (detached view of ``base_red_incidents``)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BaseRedIncident(BaseModel):
    """A red integration-base incident, as consumed by the submission gate."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    base_branch: str
    status: str
    consecutive_red: int
    owner_task_id: UUID | None = None
    owner_agent: str | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    closed_reason: str | None = None
    cleared_by: str | None = None
