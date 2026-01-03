"""
Proactive Knowledge Injection Service

Automatically injects relevant context when:
- An agent claims a task
- An agent starts a new session

Searches for:
- Similar past tasks and their learnings
- Relevant code patterns
- Applicable standards
- Recent team decisions
- Known issues related to the work
"""

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import structlog

from roboco.models.optimal import IndexType, QueryContext, SearchResult

logger = structlog.get_logger()


@dataclass
class ContextPackage:
    """
    A package of relevant context for an agent.

    Assembled from multiple knowledge sources and
    ready to be injected into the agent's session.
    """

    task_id: UUID | None = None
    agent_id: UUID | None = None
    similar_tasks: list[SearchResult] = field(default_factory=list)
    relevant_learnings: list[SearchResult] = field(default_factory=list)
    code_patterns: list[SearchResult] = field(default_factory=list)
    applicable_standards: list[SearchResult] = field(default_factory=list)
    recent_decisions: list[SearchResult] = field(default_factory=list)
    known_issues: list[SearchResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task_id": str(self.task_id) if self.task_id else None,
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "similar_tasks": [self._result_to_dict(r) for r in self.similar_tasks],
            "relevant_learnings": [
                self._result_to_dict(r) for r in self.relevant_learnings
            ],
            "code_patterns": [self._result_to_dict(r) for r in self.code_patterns],
            "applicable_standards": [
                self._result_to_dict(r) for r in self.applicable_standards
            ],
            "recent_decisions": [
                self._result_to_dict(r) for r in self.recent_decisions
            ],
            "known_issues": [self._result_to_dict(r) for r in self.known_issues],
            "summary": self.summary,
        }

    def _result_to_dict(self, result: SearchResult) -> dict[str, Any]:
        """Convert a SearchResult to dict."""
        return {
            "content": result.content,
            "source": result.source,
            "score": result.score,
            "index_type": result.index_type.value,
            "metadata": result.metadata,
        }

    def is_empty(self) -> bool:
        """Check if the package has any content."""
        return not any(
            [
                self.similar_tasks,
                self.relevant_learnings,
                self.code_patterns,
                self.applicable_standards,
                self.recent_decisions,
                self.known_issues,
            ]
        )


class ProactiveKnowledgeService:
    """
    Service for proactively injecting relevant knowledge.

    Triggered by:
    - TaskService.claim_task() -> on_task_claimed()
    - SessionService.start_session() -> on_session_started()

    Assembles context from multiple indexes and provides
    it to the agent before they begin work.
    """

    def __init__(self) -> None:
        """Initialize ProactiveKnowledgeService."""
        self._optimal_service: Any = None

    async def initialize(self, optimal_service: Any) -> None:
        """
        Initialize with OptimalService reference.

        Args:
            optimal_service: The OptimalService instance
        """
        self._optimal_service = optimal_service
        logger.info("ProactiveKnowledgeService initialized")

    async def on_task_claimed(
        self,
        task_id: UUID,
        agent_id: UUID,
        task_title: str,
        task_description: str,
        task_type: str | None = None,
    ) -> ContextPackage:
        """
        Build context package when an agent claims a task.

        Searches for:
        1. Similar past tasks
        2. Learnings from those tasks
        3. Relevant code patterns
        4. Applicable standards
        5. Recent decisions

        Args:
            task_id: ID of the claimed task
            agent_id: ID of the claiming agent
            task_title: Title of the task
            task_description: Full task description
            task_type: Optional task type (feature, bug, refactor, etc.)

        Returns:
            ContextPackage with relevant knowledge
        """
        if not self._optimal_service:
            raise RuntimeError("ProactiveKnowledgeService not initialized")

        package = ContextPackage(task_id=task_id, agent_id=agent_id)

        # Build search query from task info
        query = f"{task_title}\n{task_description}"

        # 1. Find similar past tasks from journals
        try:
            package.similar_tasks = await self._search_similar_tasks(query)
        except Exception as e:
            logger.warning("Failed to search similar tasks", error=str(e))

        # 2. Get learnings from similar tasks
        try:
            package.relevant_learnings = await self._get_relevant_learnings(query)
        except Exception as e:
            logger.warning("Failed to get learnings", error=str(e))

        # 3. Find relevant code patterns
        try:
            package.code_patterns = await self._find_code_patterns(query)
        except Exception as e:
            logger.warning("Failed to find code patterns", error=str(e))

        # 4. Get applicable standards
        try:
            domain = self._infer_domain(task_type, task_description)
            package.applicable_standards = await self._get_applicable_standards(domain)
        except Exception as e:
            logger.warning("Failed to get standards", error=str(e))

        # 5. Find recent relevant decisions
        try:
            package.recent_decisions = await self._find_relevant_decisions(query)
        except Exception as e:
            logger.warning("Failed to find decisions", error=str(e))

        # 6. Check for known issues
        try:
            package.known_issues = await self._check_known_issues(query)
        except Exception as e:
            logger.warning("Failed to check known issues", error=str(e))

        # Build summary
        package.summary = self._build_summary(package)

        logger.info(
            "Built context package for task",
            task_id=str(task_id),
            agent_id=str(agent_id),
            items_found=self._count_items(package),
        )

        return package

    async def on_session_started(
        self,
        session_id: UUID,
        agent_id: UUID,
        agent_role: str,
        scope: str | None = None,
    ) -> ContextPackage:
        """
        Build context package when an agent starts a session.

        Provides:
        1. Recent learnings from the agent's team
        2. Recent decisions affecting their work
        3. Unresolved issues in their domain
        4. Key standards for their role

        Args:
            session_id: ID of the new session
            agent_id: ID of the agent
            agent_role: Role of the agent (developer, qa, pm, etc.)
            scope: Optional scope (backend, frontend, etc.)

        Returns:
            ContextPackage with session context
        """
        if not self._optimal_service:
            raise RuntimeError("ProactiveKnowledgeService not initialized")

        package = ContextPackage(agent_id=agent_id)

        # Build query based on role and scope
        query = f"{agent_role} {scope or ''}"

        # 1. Recent team learnings
        try:
            package.relevant_learnings = await self._get_team_learnings(
                agent_role, scope
            )
        except Exception as e:
            logger.warning("Failed to get team learnings", error=str(e))

        # 2. Recent decisions
        try:
            package.recent_decisions = await self._get_recent_decisions(scope)
        except Exception as e:
            logger.warning("Failed to get recent decisions", error=str(e))

        # 3. Key standards for role
        try:
            domain = self._role_to_domain(agent_role)
            package.applicable_standards = await self._get_applicable_standards(domain)
        except Exception as e:
            logger.warning("Failed to get standards", error=str(e))

        # 4. Unresolved issues in domain
        try:
            package.known_issues = await self._check_known_issues(query)
        except Exception as e:
            logger.warning("Failed to check known issues", error=str(e))

        # Build summary
        package.summary = self._build_summary(package)

        logger.info(
            "Built context package for session",
            session_id=str(session_id),
            agent_id=str(agent_id),
            agent_role=agent_role,
            items_found=self._count_items(package),
        )

        return package

    async def get_context_for_task(
        self,
        task_id: UUID,
        agent_id: UUID,
    ) -> ContextPackage:
        """
        Get proactive context for a task.

        Handles task lookup internally - routes should not query DB directly.

        Args:
            task_id: The task ID to get context for
            agent_id: The requesting agent's ID

        Returns:
            ContextPackage with relevant knowledge
        """
        if not self._optimal_service:
            raise RuntimeError("ProactiveKnowledgeService not initialized")

        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import TaskTable

        async with get_db_context() as db:
            result = await db.execute(select(TaskTable).where(TaskTable.id == task_id))
            task = result.scalar_one_or_none()

            if not task:
                # Return empty package if task not found
                return ContextPackage(task_id=task_id, agent_id=agent_id)

            return await self.on_task_claimed(
                task_id=task_id,
                agent_id=agent_id,
                task_title=task.title,
                task_description=task.description or "",
                task_type=None,
            )

    async def get_context_for_session(
        self,
        session_id: UUID,
        agent_id: UUID,
    ) -> ContextPackage:
        """
        Get proactive context for a session.

        Handles agent lookup internally - routes should not query DB directly.

        Args:
            session_id: The session ID
            agent_id: The agent's ID

        Returns:
            ContextPackage with relevant knowledge
        """
        if not self._optimal_service:
            raise RuntimeError("ProactiveKnowledgeService not initialized")

        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import AgentTable

        async with get_db_context() as db:
            result = await db.execute(
                select(AgentTable).where(AgentTable.id == agent_id)
            )
            agent = result.scalar_one_or_none()

            if not agent:
                # Return empty package if agent not found
                return ContextPackage(agent_id=agent_id)

            return await self.on_session_started(
                session_id=session_id,
                agent_id=agent_id,
                agent_role=agent.role.value if agent.role else "developer",
                scope=agent.team.value if agent.team else None,
            )

    async def _search_similar_tasks(
        self, query: str, top_k: int = 5
    ) -> list[SearchResult]:
        """Search for similar past tasks in journals."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search(
                query=query,
                context=QueryContext(index_types=[IndexType.JOURNALS]),
                top_k=top_k,
            ),
        )

    async def _get_relevant_learnings(
        self, query: str, top_k: int = 5
    ) -> list[SearchResult]:
        """Get learnings relevant to the query."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search_learnings(
                query=query,
                top_k=top_k,
            ),
        )

    async def _find_code_patterns(
        self, query: str, top_k: int = 3
    ) -> list[SearchResult]:
        """DEPRECATED: Code indexing has been removed."""
        _ = query, top_k  # Unused
        return []  # Code index deprecated

    async def _get_applicable_standards(
        self, domain: str, top_k: int = 5
    ) -> list[SearchResult]:
        """Get standards for a domain."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.get_standards(
                domain=domain,
                top_k=top_k,
            ),
        )

    async def _find_relevant_decisions(
        self, query: str, top_k: int = 3
    ) -> list[SearchResult]:
        """Find decisions relevant to the query."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search(
                query=query,
                context=QueryContext(index_types=[IndexType.DECISIONS]),
                top_k=top_k,
            ),
        )

    async def _check_known_issues(
        self, query: str, top_k: int = 3
    ) -> list[SearchResult]:
        """Check for known issues related to the query."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search_errors(
                error_message=query,
                top_k=top_k,
            ),
        )

    async def _get_team_learnings(
        self, role: str, scope: str | None, top_k: int = 5
    ) -> list[SearchResult]:
        """Get recent learnings from agents with similar roles."""
        query = f"{role} learnings"
        if scope:
            query = f"{scope} {query}"
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search_learnings(
                query=query,
                top_k=top_k,
            ),
        )

    async def _get_recent_decisions(
        self, scope: str | None, top_k: int = 5
    ) -> list[SearchResult]:
        """Get recent decisions relevant to the scope."""
        query = scope or "recent decisions"
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search(
                query=query,
                context=QueryContext(index_types=[IndexType.DECISIONS]),
                top_k=top_k,
            ),
        )

    def _infer_domain(self, task_type: str | None, description: str) -> str:
        """Infer the domain from task type and description."""
        desc_lower = description.lower()

        if task_type:
            type_lower = task_type.lower()
            if "security" in type_lower:
                return "security"
            if "workflow" in type_lower or "process" in type_lower:
                return "workflow"

        # Infer from description
        if any(kw in desc_lower for kw in ["auth", "security", "encrypt", "password"]):
            return "security"
        if any(kw in desc_lower for kw in ["workflow", "process", "task", "kanban"]):
            return "workflow"

        return "coding"

    def _role_to_domain(self, role: str) -> str:
        """Map agent role to a standards domain."""
        role_lower = role.lower()

        if any(kw in role_lower for kw in ["qa", "test", "quality"]):
            return "workflow"
        if any(kw in role_lower for kw in ["pm", "manager", "product"]):
            return "workflow"
        if any(kw in role_lower for kw in ["security", "sec"]):
            return "security"

        return "coding"

    def _build_summary(self, package: ContextPackage) -> str:
        """Build a human-readable summary of the context package."""
        parts = []

        if package.similar_tasks:
            parts.append(f"Found {len(package.similar_tasks)} similar past tasks")

        if package.relevant_learnings:
            parts.append(f"Found {len(package.relevant_learnings)} relevant learnings")

        if package.code_patterns:
            parts.append(f"Found {len(package.code_patterns)} code patterns")

        if package.applicable_standards:
            parts.append(f"{len(package.applicable_standards)} standards apply")

        if package.recent_decisions:
            parts.append(f"{len(package.recent_decisions)} relevant decisions found")

        if package.known_issues:
            parts.append(f"Warning: {len(package.known_issues)} known issues may apply")

        if not parts:
            return "No relevant context found"

        return ". ".join(parts) + "."

    def _count_items(self, package: ContextPackage) -> int:
        """Count total items in the package."""
        return sum(
            [
                len(package.similar_tasks),
                len(package.relevant_learnings),
                len(package.code_patterns),
                len(package.applicable_standards),
                len(package.recent_decisions),
                len(package.known_issues),
            ]
        )


class _ProactiveServiceHolder:
    """Holder for singleton ProactiveKnowledgeService instance."""

    instance: ProactiveKnowledgeService | None = None


async def get_proactive_service() -> ProactiveKnowledgeService:
    """Get or create the ProactiveKnowledgeService instance."""
    if _ProactiveServiceHolder.instance is None:
        from roboco.services.optimal import get_optimal_service

        _ProactiveServiceHolder.instance = ProactiveKnowledgeService()
        optimal = await get_optimal_service()
        await _ProactiveServiceHolder.instance.initialize(optimal)
    return _ProactiveServiceHolder.instance
