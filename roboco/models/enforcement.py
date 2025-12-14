"""
Enforcement Models

Domain types for enforcement rules.
"""

from dataclasses import dataclass


@dataclass
class TaskClaimContext:
    """Context for validating a task claim."""

    agent_id: str
    task_id: str
    task_status: str
    task_team: str
    agent_active_tasks: list[dict]
    agent_paused_tasks: list[dict]


@dataclass
class OwnershipContext:
    """Context for validating task ownership."""

    agent_id: str
    task_id: str
    current_owner: str | None
    current_status: str
