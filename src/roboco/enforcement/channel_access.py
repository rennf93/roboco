"""
Channel Access Enforcement

Validates agent access to channels based on predefined access rules.
"""

from roboco.agents_config import CHANNEL_ACCESS
from roboco.exceptions import RobocoError


class ChannelAccessDeniedError(RobocoError):
    """Raised when an agent doesn't have access to a channel."""

    def __init__(
        self,
        agent_id: str,
        channel_slug: str,
        action: str,
        message: str | None = None,
    ):
        self.agent_id = agent_id
        self.channel_slug = channel_slug
        self.action = action
        super().__init__(
            code="CHANNEL_ACCESS_DENIED",
            message=message
            or f"Agent {agent_id} cannot {action} in channel #{channel_slug}",
            details={
                "agent_id": agent_id,
                "channel_slug": channel_slug,
                "action": action,
            },
        )


def validate_channel_access(
    agent_id: str,
    channel_slug: str,
    action: str,
) -> bool:
    """
    Validate agent can perform action on channel.

    Args:
        agent_id: The agent attempting access
        channel_slug: The channel slug being accessed
        action: "read" or "write"

    Returns:
        True if allowed

    Raises:
        ChannelAccessDeniedError: If access denied
    """
    if action not in ("read", "write"):
        raise ValueError(f"Invalid action: {action}. Must be 'read' or 'write'")

    channel = CHANNEL_ACCESS.get(channel_slug)
    if not channel:
        # Unknown channel - allow by default (will be caught by other validation)
        return True

    allowed = channel.get(action, [])

    # Wildcard allows everyone
    if "*" in allowed:
        return True

    # Direct access
    if agent_id in allowed:
        return True

    # Silent observers can always read (but not write)
    if action == "read" and agent_id in channel.get("silent", []):
        return True

    raise ChannelAccessDeniedError(
        agent_id=agent_id,
        channel_slug=channel_slug,
        action=action,
    )


def get_agent_channels(agent_id: str, action: str = "read") -> list[str]:
    """
    Get list of channels an agent has access to.

    Args:
        agent_id: The agent identifier
        action: "read" or "write"

    Returns:
        List of channel slugs
    """
    channels = []
    for slug, access in CHANNEL_ACCESS.items():
        allowed = access.get(action, [])
        if (
            "*" in allowed
            or agent_id in allowed
            or (action == "read" and agent_id in access.get("silent", []))
        ):
            channels.append(slug)
    return channels
