"""
A2A Access Enforcement

Validates agent-to-agent communication permissions.
Uses the same communication matrix as channels/notifications.
"""

from roboco.agents_config import can_a2a_direct, get_a2a_route_hint
from roboco.exceptions import RobocoError


class A2AAccessDeniedError(RobocoError):
    """Raised when A2A communication is not permitted."""

    def __init__(
        self,
        from_agent: str,
        to_agent: str,
        reason: str,
        route_hint: str | None = None,
    ):
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.reason = reason
        self.route_hint = route_hint
        super().__init__(
            code="A2A_ACCESS_DENIED",
            message=f"{from_agent} cannot A2A with {to_agent}: {reason}",
            details={
                "from_agent": from_agent,
                "to_agent": to_agent,
                "reason": reason,
                "route_hint": route_hint,
            },
        )


def validate_a2a_access(from_agent: str, to_agent: str) -> bool:
    """
    Validate that from_agent can initiate A2A with to_agent.

    Args:
        from_agent: The agent initiating the A2A (slug)
        to_agent: The target agent (slug)

    Returns:
        True if allowed

    Raises:
        A2AAccessDeniedError: If A2A not permitted
    """
    if from_agent == to_agent:
        raise A2AAccessDeniedError(
            from_agent=from_agent,
            to_agent=to_agent,
            reason="cannot A2A yourself — use your own journal or task notes instead",
        )

    allowed, error = can_a2a_direct(from_agent, to_agent)

    if not allowed:
        route_hint = get_a2a_route_hint(from_agent, to_agent)
        raise A2AAccessDeniedError(
            from_agent=from_agent,
            to_agent=to_agent,
            reason=error or "A2A not permitted",
            route_hint=route_hint,
        )

    return True


def get_a2a_allowed_targets(from_agent: str, all_agents: list[str]) -> list[str]:
    """
    Get list of agents that from_agent can A2A with.

    Args:
        from_agent: The agent to check permissions for
        all_agents: List of all agent slugs to check against

    Returns:
        List of agent slugs that from_agent can A2A with
    """
    allowed = []
    for target in all_agents:
        if target == from_agent:
            continue  # Can't A2A with yourself
        is_allowed, _ = can_a2a_direct(from_agent, target)
        if is_allowed:
            allowed.append(target)
    return allowed
