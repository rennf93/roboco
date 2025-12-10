"""
Agent Orchestrator

Manages the lifecycle of all agents in the system.
Handles spawning, monitoring, and coordination.
"""

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, AgentStatus, Team
import contextlib

logger = structlog.get_logger()


class Orchestrator:
    """
    Central orchestrator for all RoboCo agents.

    Responsibilities:
    - Spawn and stop agents
    - Monitor agent health
    - Route messages between agents
    - Handle agent failures and restarts
    """

    def __init__(self) -> None:
        """Initialize the orchestrator."""
        self._agents: dict[UUID, Agent] = {}
        self._agent_tasks: dict[UUID, asyncio.Task] = {}
        self._running = False
        self._monitor_task: asyncio.Task | None = None

        self.log = logger.bind(component="orchestrator")

    @property
    def agents(self) -> dict[UUID, Agent]:
        """Get all registered agents."""
        return self._agents.copy()

    @property
    def active_agents(self) -> list[Agent]:
        """Get all active agents."""
        return [
            a for a in self._agents.values() if a.state.status == AgentStatus.ACTIVE
        ]

    @property
    def idle_agents(self) -> list[Agent]:
        """Get all idle agents."""
        return [a for a in self._agents.values() if a.state.status == AgentStatus.IDLE]

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the orchestrator."""
        if self._running:
            self.log.warning("Orchestrator already running")
            return

        self.log.info("Starting orchestrator")
        self._running = True

        # Start health monitor
        self._monitor_task = asyncio.create_task(self._health_monitor())

    async def stop(self) -> None:
        """Stop the orchestrator and all agents."""
        if not self._running:
            return

        self.log.info("Stopping orchestrator")
        self._running = False

        # Stop health monitor
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        # Stop all agents
        await self.stop_all_agents()

        self.log.info("Orchestrator stopped")

    # =========================================================================
    # AGENT MANAGEMENT
    # =========================================================================

    def register_agent(self, agent: Agent) -> None:
        """
        Register an agent with the orchestrator.

        Args:
            agent: The agent to register
        """
        if agent.id in self._agents:
            self.log.warning("Agent already registered", agent_id=str(agent.id))
            return

        self._agents[agent.id] = agent
        self.log.info(
            "Agent registered",
            agent_id=str(agent.id),
            agent_name=agent.name,
            agent_role=agent.role.value,
        )

    def unregister_agent(self, agent_id: UUID) -> None:
        """
        Unregister an agent.

        Args:
            agent_id: ID of the agent to unregister
        """
        if agent_id not in self._agents:
            return

        agent = self._agents.pop(agent_id)
        self.log.info(
            "Agent unregistered", agent_id=str(agent_id), agent_name=agent.name
        )

    async def spawn_agent(self, agent: Agent) -> None:
        """
        Spawn an agent (register and start).

        Args:
            agent: The agent to spawn
        """
        self.register_agent(agent)
        await agent.start()
        self.log.info("Agent spawned", agent_id=str(agent.id), agent_name=agent.name)

    async def stop_agent(self, agent_id: UUID) -> None:
        """
        Stop a specific agent.

        Args:
            agent_id: ID of the agent to stop
        """
        if agent_id not in self._agents:
            self.log.warning("Agent not found", agent_id=str(agent_id))
            return

        agent = self._agents[agent_id]
        await agent.stop()
        self.log.info("Agent stopped", agent_id=str(agent_id), agent_name=agent.name)

    async def stop_all_agents(self) -> None:
        """Stop all registered agents."""
        self.log.info("Stopping all agents", count=len(self._agents))

        # Stop all agents concurrently
        await asyncio.gather(
            *[agent.stop() for agent in self._agents.values()],
            return_exceptions=True,
        )

    async def restart_agent(self, agent_id: UUID) -> None:
        """
        Restart an agent.

        Args:
            agent_id: ID of the agent to restart
        """
        if agent_id not in self._agents:
            self.log.warning("Agent not found", agent_id=str(agent_id))
            return

        agent = self._agents[agent_id]
        self.log.info("Restarting agent", agent_id=str(agent_id), agent_name=agent.name)

        await agent.stop()
        await asyncio.sleep(1)  # Brief pause
        await agent.start()

    # =========================================================================
    # QUERYING
    # =========================================================================

    def get_agent(self, agent_id: UUID) -> Agent | None:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_agent_by_slug(self, slug: str) -> Agent | None:
        """Get an agent by slug."""
        for agent in self._agents.values():
            if agent.config.slug == slug:
                return agent
        return None

    def get_agents_by_role(self, role: AgentRole) -> list[Agent]:
        """Get all agents with a specific role."""
        return [a for a in self._agents.values() if a.role == role]

    def get_agents_by_team(self, team: Team) -> list[Agent]:
        """Get all agents in a specific team."""
        return [a for a in self._agents.values() if a.team == team]

    def get_cell_agents(self, team: Team) -> dict[str, list[Agent]]:
        """
        Get agents organized by role for a team/cell.

        Returns:
            Dict with keys: developers, qa, pm, documenter
        """
        team_agents = self.get_agents_by_team(team)
        return {
            "developers": [a for a in team_agents if a.role == AgentRole.DEVELOPER],
            "qa": [a for a in team_agents if a.role == AgentRole.QA],
            "pm": [a for a in team_agents if a.role == AgentRole.CELL_PM],
            "documenter": [a for a in team_agents if a.role == AgentRole.DOCUMENTER],
        }

    # =========================================================================
    # HEALTH MONITORING
    # =========================================================================

    async def _health_monitor(self) -> None:
        """
        Monitor agent health periodically.

        Checks for:
        - Unresponsive agents
        - Agents with errors
        - Agents that need restart
        """
        while self._running:
            try:
                await self._check_agent_health()
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error("Error in health monitor", error=str(e))
                await asyncio.sleep(5)

    async def _check_agent_health(self) -> None:
        """Check health of all agents."""
        now = datetime.utcnow()

        for agent in self._agents.values():
            # Check for errors
            if agent.state.error:
                self.log.warning(
                    "Agent has error",
                    agent_id=str(agent.id),
                    error=agent.state.error,
                )

            # Check for inactivity (5 minutes)
            if agent.state.last_activity:
                inactive_seconds = (now - agent.state.last_activity).total_seconds()
                if inactive_seconds > 300 and agent.is_running:
                    self.log.warning(
                        "Agent inactive",
                        agent_id=str(agent.id),
                        inactive_seconds=inactive_seconds,
                    )

    def get_health_status(self) -> dict[str, Any]:
        """
        Get overall health status of all agents.

        Returns:
            Health status summary
        """
        total = len(self._agents)
        by_status = {}
        errors = []

        for agent in self._agents.values():
            status = agent.state.status.value
            by_status[status] = by_status.get(status, 0) + 1

            if agent.state.error:
                errors.append(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "error": agent.state.error,
                    }
                )

        return {
            "total_agents": total,
            "by_status": by_status,
            "errors": errors,
            "healthy": len(errors) == 0,
        }

    # =========================================================================
    # CELL MANAGEMENT
    # =========================================================================

    async def spawn_cell(
        self,
        team: Team,
        agent_factory: callable,
    ) -> list[Agent]:
        """
        Spawn all agents for a cell.

        Args:
            team: The team/cell to spawn
            agent_factory: Factory function that creates agents for the team

        Returns:
            List of spawned agents
        """
        self.log.info("Spawning cell", team=team.value)

        agents = agent_factory(team)
        for agent in agents:
            await self.spawn_agent(agent)

        return agents

    async def stop_cell(self, team: Team) -> None:
        """
        Stop all agents in a cell.

        Args:
            team: The team/cell to stop
        """
        self.log.info("Stopping cell", team=team.value)

        team_agents = self.get_agents_by_team(team)
        await asyncio.gather(
            *[self.stop_agent(a.id) for a in team_agents],
            return_exceptions=True,
        )

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def to_dict(self) -> dict[str, Any]:
        """Get orchestrator status as dictionary."""
        return {
            "running": self._running,
            "total_agents": len(self._agents),
            "agents": [a.to_dict() for a in self._agents.values()],
            "health": self.get_health_status(),
        }


# =============================================================================
# GLOBAL ORCHESTRATOR INSTANCE
# =============================================================================

# Singleton orchestrator for the application
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Get or create the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


async def start_orchestrator() -> Orchestrator:
    """Start the global orchestrator."""
    orchestrator = get_orchestrator()
    await orchestrator.start()
    return orchestrator


async def stop_orchestrator() -> None:
    """Stop the global orchestrator."""
    global _orchestrator
    if _orchestrator:
        await _orchestrator.stop()
        _orchestrator = None
