"""
Repository Pattern for Database Operations

Provides abstract repository interface and common implementations
for database operations. Reduces boilerplate in services.
"""

from roboco.services.repositories.base import BaseRepository
from roboco.services.repositories.query_helpers import (
    agent_id_filter,
    get_agent_slug,
    pagination,
    resolve_agent_identity,
    resolve_agent_uuid,
    status_filter,
    team_filter,
    timestamp_filter,
)

__all__ = [
    "BaseRepository",
    "agent_id_filter",
    "get_agent_slug",
    "pagination",
    "resolve_agent_identity",
    "resolve_agent_uuid",
    "status_filter",
    "team_filter",
    "timestamp_filter",
]
