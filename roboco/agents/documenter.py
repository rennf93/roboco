"""
Documenter Agent

Implementation of the Documenter workflow from the blueprint.
Handles documentation lifecycle:
    MONITOR → RECEIVE → GATHER → SYNTHESIZE → WRITE → REVIEW → PUBLISH
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import UUID

import aiofiles
import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, TaskStatus, Team

logger = structlog.get_logger()


class DocTaskPhase(str, Enum):
    """Phases of the Documenter lifecycle."""

    MONITOR = "monitor"
    RECEIVE = "receive"
    GATHER = "gather"
    SYNTHESIZE = "synthesize"
    WRITE = "write"
    REVIEW = "review"
    PUBLISH = "publish"


class DocType(str, Enum):
    """Types of documentation."""

    API = "api"
    README = "readme"
    ARCHITECTURE = "architecture"
    CHANGELOG = "changelog"
    KNOWLEDGE_BASE = "knowledge_base"
    COMPONENT = "component"
    DESIGN_SYSTEM = "design_system"


@dataclass
class DocumentSpec:
    """Specification for a document to create/update."""

    doc_type: DocType
    title: str
    path: str
    priority: str = "required"  # required, optional
    content: str | None = None


@dataclass
class DocContext:
    """Context for the current documentation task."""

    task_id: UUID
    title: str
    phase: DocTaskPhase = DocTaskPhase.RECEIVE
    # Gathered materials
    dev_notes: str | None = None
    qa_feedback: str | None = None
    commits: list[str] = field(default_factory=list)
    conversations: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)
    # Synthesis
    summary: str | None = None
    documents_needed: list[DocumentSpec] = field(default_factory=list)
    current_doc: int = 0
    # Output
    written_docs: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now(UTC))
    notes: list[str] = field(default_factory=list)


class DocumenterAgent(Agent):
    """
    Documenter agent that follows the Documenter Lifecycle.

    Workflow:
    1. MONITOR - Watch cell channel, follow development
    2. RECEIVE - Dev creates handoff, PM notifies
    3. GATHER - Pull notes, commits, conversations, QA feedback
    4. SYNTHESIZE - Understand what was built, identify docs needed
    5. WRITE - Create/update documentation
    6. REVIEW - Self-review, optional dev review
    7. PUBLISH - Documentation goes live
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize documenter agent."""
        super().__init__(config)
        self._doc_context: DocContext | None = None
        self._cell_channel_id: UUID | None = None
        self._pending_docs: list[UUID] = []

    @property
    def cell_name(self) -> str:
        """Get the cell name based on team."""
        if self.team == Team.BACKEND:
            return "backend-cell"
        elif self.team == Team.FRONTEND:
            return "frontend-cell"
        elif self.team == Team.UX_UI:
            return "uxui-cell"
        return "unknown-cell"

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """
        MONITOR phase: Watch for documentation requests.

        - Check for tasks awaiting documentation
        - Check for documentation notifications
        """
        self.log.info("Monitoring for documentation requests")

        if self._pending_docs:
            return self._pending_docs.pop(0)

        task_id = await self._find_awaiting_documentation()
        if task_id:
            return task_id

        return None

    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute documentation through lifecycle phases.

        Returns True when documentation is complete.
        """
        if self._doc_context is None or self._doc_context.task_id != task_id:
            self._doc_context = DocContext(
                task_id=task_id,
                title=await self._get_task_title(task_id),
            )

        ctx = self._doc_context

        try:
            match ctx.phase:
                case DocTaskPhase.RECEIVE:
                    await self._phase_receive(ctx)
                    ctx.phase = DocTaskPhase.GATHER

                case DocTaskPhase.GATHER:
                    await self._phase_gather(ctx)
                    ctx.phase = DocTaskPhase.SYNTHESIZE

                case DocTaskPhase.SYNTHESIZE:
                    await self._phase_synthesize(ctx)
                    ctx.phase = DocTaskPhase.WRITE

                case DocTaskPhase.WRITE:
                    completed = await self._phase_write(ctx)
                    if completed:
                        ctx.phase = DocTaskPhase.REVIEW

                case DocTaskPhase.REVIEW:
                    await self._phase_review(ctx)
                    ctx.phase = DocTaskPhase.PUBLISH

                case DocTaskPhase.PUBLISH:
                    await self._phase_publish(ctx)
                    self._doc_context = None
                    return True

            return False

        except Exception as e:
            self.log.error("Error in doc phase", phase=ctx.phase.value, error=str(e))
            return False

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_receive(self, ctx: DocContext) -> None:
        """
        RECEIVE phase: Claim documentation task.
        """
        self.log.info("RECEIVE phase", task_id=str(ctx.task_id))

        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"Starting documentation for TASK-{str(ctx.task_id)[:8]}: {ctx.title}",
            message_type="action",
        )

        ctx.notes.append(f"[{datetime.now(UTC).isoformat()}] Documentation started")

    async def _phase_gather(self, ctx: DocContext) -> None:
        """
        GATHER phase: Collect all materials.

        - Pull dev's journey notes
        - Pull commits
        - Pull conversations
        - Pull QA feedback
        - Review code changes
        """
        self.log.info("GATHER phase", task_id=str(ctx.task_id))

        # Gather all materials
        ctx.dev_notes = await self._read_dev_notes(ctx.task_id)
        ctx.qa_feedback = await self._read_qa_feedback(ctx.task_id)
        ctx.commits = await self._get_task_commits(ctx.task_id)
        ctx.conversations = await self._get_conversations(ctx.task_id)
        ctx.code_changes = await self._get_code_changes(ctx.task_id)

        ctx.notes.append(
            f"[{datetime.now(UTC).isoformat()}] Gathered materials: "
            f"{len(ctx.commits)} commits, {len(ctx.conversations)} conversations"
        )

    async def _phase_synthesize(self, ctx: DocContext) -> None:
        """
        SYNTHESIZE phase: Understand and identify docs needed.

        - What was built
        - Why decisions were made
        - What needs documenting
        """
        self.log.info("SYNTHESIZE phase", task_id=str(ctx.task_id))

        prompt = f"""
Analyze this completed task and determine what documentation is needed.

Task: {ctx.title}

Developer Notes:
{ctx.dev_notes or "None provided"}

QA Feedback:
{ctx.qa_feedback or "None provided"}

Commits:
{chr(10).join(ctx.commits) if ctx.commits else "None"}

Code Changes:
{chr(10).join(ctx.code_changes) if ctx.code_changes else "None"}

Determine:
1. Summary of what was built
2. Key decisions made
3. Documentation needed:
   - API docs? (if new/changed endpoints)
   - README updates? (if usage changed)
   - Architecture docs? (if structure changed)
   - Changelog entry? (always for features)
   - Knowledge base? (for reusable learnings)

Respond with structured analysis.
"""
        response = await self.think(prompt)
        ctx.summary = response

        # Determine documents needed (simplified)
        ctx.documents_needed = [
            DocumentSpec(
                doc_type=DocType.CHANGELOG,
                title=f"Changelog entry for {ctx.title}",
                path="CHANGELOG.md",
                priority="required",
            ),
        ]

        # Add API docs if backend
        if self.team == Team.BACKEND:
            ctx.documents_needed.append(
                DocumentSpec(
                    doc_type=DocType.API,
                    title=f"API documentation for {ctx.title}",
                    path="docs/api/",
                    priority="required",
                )
            )

        # Add component docs if frontend
        if self.team == Team.FRONTEND:
            ctx.documents_needed.append(
                DocumentSpec(
                    doc_type=DocType.COMPONENT,
                    title=f"Component documentation for {ctx.title}",
                    path="docs/components/",
                    priority="required",
                )
            )

        ctx.notes.append(
            f"[{datetime.now(UTC).isoformat()}] Synthesis complete: "
            f"{len(ctx.documents_needed)} documents needed"
        )

    async def _phase_write(self, ctx: DocContext) -> bool:
        """
        WRITE phase: Create/update documentation.

        Returns True when all docs written.
        """
        self.log.info(
            "WRITE phase",
            task_id=str(ctx.task_id),
            doc=ctx.current_doc,
            total=len(ctx.documents_needed),
        )

        if ctx.current_doc >= len(ctx.documents_needed):
            return True

        doc_spec = ctx.documents_needed[ctx.current_doc]

        # Use TOON for token-efficient context encoding
        doc_context = self.format_context_labeled(
            "Documentation Task",
            {
                "title": ctx.title,
                "doc_type": doc_spec.doc_type.value,
                "target_path": doc_spec.path,
                "summary": ctx.summary,
                "dev_notes": ctx.dev_notes or "None",
            },
        )

        prompt = f"""Write documentation for this task.

{doc_context}

Write professional, clear documentation following best practices.
Include:
- Clear description
- Usage examples (if applicable)
- Code samples (if applicable)
- Any gotchas or notes

Format appropriately for the document type.
"""
        content = await self.think(prompt)
        doc_spec.content = content
        ctx.written_docs.append(doc_spec.path)

        ctx.current_doc += 1

        progress = f"{ctx.current_doc}/{len(ctx.documents_needed)}"
        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} doc {progress}: {doc_spec.title}",
            message_type="action",
        )

        return ctx.current_doc >= len(ctx.documents_needed)

    async def _phase_review(self, ctx: DocContext) -> None:
        """
        REVIEW phase: Self-review documentation.

        - Review for accuracy
        - Optional dev review
        """
        self.log.info("REVIEW phase", task_id=str(ctx.task_id))

        # Self-review using LLM
        for doc_spec in ctx.documents_needed:
            if not doc_spec.content:
                continue

            # Use TOON for token-efficient context encoding
            review_context = self.format_context_labeled(
                "Document Review",
                {
                    "title": doc_spec.title,
                    "doc_type": doc_spec.doc_type.value,
                    "content": doc_spec.content,
                },
            )

            prompt = f"""Review this documentation for quality:

{review_context}

Check:
1. Accuracy - Does it correctly describe the feature?
2. Completeness - Is anything missing?
3. Clarity - Is it easy to understand?
4. Examples - Are examples helpful and correct?

Format response as TOON:
{{accuracy,completeness,clarity,examples,suggestions}}:
good,complete,clear,helpful,None
"""
            review = await self.think(prompt)
            ts = datetime.now(UTC).isoformat()
            ctx.notes.append(f"[{ts}] Reviewed {doc_spec.title}: {review[:100]}...")

    async def _phase_publish(self, ctx: DocContext) -> None:
        """
        PUBLISH phase: Documentation goes live.

        - Write files to disk
        - Link docs to task
        - Update task status
        """
        self.log.info("PUBLISH phase", task_id=str(ctx.task_id))

        # Write documentation files
        for doc_spec in ctx.documents_needed:
            if doc_spec.content:
                try:
                    path = Path(doc_spec.path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(path, "w") as f:
                        await f.write(doc_spec.content)
                    self.log.info("Published", path=doc_spec.path)
                except Exception as e:
                    self.log.error(
                        "Failed to publish", path=doc_spec.path, error=str(e)
                    )

        # Update task status
        await self._update_task_status(ctx.task_id, TaskStatus.COMPLETED)

        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} documentation complete\n"
            f"Published: {', '.join(ctx.written_docs)}",
            message_type="action",
        )

        ctx.notes.append(f"[{datetime.now(UTC).isoformat()}] Documentation published")

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _find_awaiting_documentation(self) -> UUID | None:
        """Find tasks awaiting documentation."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "awaiting_documentation", "team": team_param},
            )
            tasks = result.get("items", [])
            return UUID(tasks[0]["id"]) if tasks else None
        except Exception as e:
            self.log.warning("Failed to find awaiting documentation task", error=str(e))
            return None

    async def _get_task_title(self, task_id: UUID) -> str:
        """Get task title."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            return result.get("title", f"Task {str(task_id)[:8]}")
        except Exception as e:
            self.log.warning("Failed to get task title", error=str(e))
            return f"Task {str(task_id)[:8]}"

    async def _read_dev_notes(self, task_id: UUID) -> str:
        """Read developer's journey notes."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            return result.get("dev_notes", "No developer notes available")
        except Exception as e:
            self.log.warning("Failed to read dev notes", error=str(e))
            return "Dev notes unavailable"

    async def _read_qa_feedback(self, task_id: UUID) -> str:
        """Read QA feedback."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            return result.get("qa_feedback", "No QA feedback available")
        except Exception as e:
            self.log.warning("Failed to read QA feedback", error=str(e))
            return "QA feedback unavailable"

    async def _get_task_commits(self, task_id: UUID) -> list[str]:
        """Get commits for the task."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            return result.get("commits", [])
        except Exception as e:
            self.log.warning("Failed to get task commits", error=str(e))
            return []

    async def _get_conversations(self, task_id: UUID) -> list[str]:
        """Get relevant conversations."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}/messages")
            messages = result.get("items", [])
            return [m.get("content", "") for m in messages]
        except Exception as e:
            self.log.warning("Failed to get conversations", error=str(e))
            return []

    async def _get_code_changes(self, task_id: UUID) -> list[str]:
        """Get code changes from commits."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            return result.get("code_changes", [])
        except Exception as e:
            self.log.warning("Failed to get code changes", error=str(e))
            return []

    async def _update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        """Update task status."""
        try:
            await self._api_call(
                "PUT",
                f"/tasks/{task_id}",
                json={"status": status.value},
            )
            self.log.info(
                "Task status updated", task_id=str(task_id), status=status.value
            )
        except Exception as e:
            self.log.error("Failed to update task status", error=str(e))


def create_backend_documenter(
    name: str = "BE-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a backend documenter agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/backend/be-documenter.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a backend documenter."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        system_prompt=system_prompt,
        capabilities=["documentation", "file_management"],
    )

    return DocumenterAgent(config)


def create_frontend_documenter(
    name: str = "FE-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a frontend documenter agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/frontend/fe-documenter.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a frontend documenter."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DOCUMENTER,
        team=Team.FRONTEND,
        system_prompt=system_prompt,
        capabilities=["documentation", "storybook", "file_management"],
    )

    return DocumenterAgent(config)


def create_ux_documenter(
    name: str = "UX-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a UX/UI documenter agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/ux_ui/ux-documenter.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a UX/UI documenter."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DOCUMENTER,
        team=Team.UX_UI,
        system_prompt=system_prompt,
        capabilities=["documentation", "design_system_docs"],
    )

    return DocumenterAgent(config)
