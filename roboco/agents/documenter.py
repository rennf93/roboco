"""
Documenter Agent

Implementation of the Documenter workflow from the blueprint.
Handles documentation lifecycle:
    MONITOR → RECEIVE → GATHER → SYNTHESIZE → WRITE → REVIEW → PUBLISH
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiofiles
import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.agents.mixins import PhaseConfig, PhaseEngine
from roboco.models import Team
from roboco.models.agents import (
    DocContext,
    DocTaskPhase,
    DocType,
    DocumentSpec,
)

logger = structlog.get_logger()


class DocumenterAgent(Agent, PhaseEngine[DocTaskPhase, DocContext]):
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

    async def _initialize(self) -> None:
        """Initialize documenter-specific resources."""
        self.log.debug("Documenter agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup documenter-specific resources."""
        self._doc_context = None
        self._pending_docs.clear()
        self.log.debug("Documenter agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # PHASE ENGINE IMPLEMENTATION
    # =========================================================================

    def _get_phase_configs(self) -> list[PhaseConfig[DocTaskPhase]]:
        """Define the documenter workflow phases."""
        return [
            PhaseConfig(
                DocTaskPhase.RECEIVE,
                self._phase_receive,
                next_phase=DocTaskPhase.GATHER,
            ),
            PhaseConfig(
                DocTaskPhase.GATHER,
                self._phase_gather,
                next_phase=DocTaskPhase.SYNTHESIZE,
            ),
            PhaseConfig(
                DocTaskPhase.SYNTHESIZE,
                self._phase_synthesize,
                next_phase=DocTaskPhase.WRITE,
            ),
            PhaseConfig(
                DocTaskPhase.WRITE,
                self._phase_write,
                next_phase=DocTaskPhase.REVIEW,
                requires_completion=True,
            ),
            PhaseConfig(
                DocTaskPhase.REVIEW,
                self._phase_review,
                next_phase=DocTaskPhase.PUBLISH,
            ),
            PhaseConfig(
                DocTaskPhase.PUBLISH,
                self._phase_publish,
                next_phase=None,  # Terminal
            ),
        ]

    def _get_current_phase(self, ctx: DocContext) -> DocTaskPhase:
        """Get the current phase from context."""
        return ctx.phase

    def _set_current_phase(self, ctx: DocContext, phase: DocTaskPhase) -> None:
        """Set the current phase in context."""
        ctx.phase = phase

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
            title, session_id = await self._get_task_info(task_id)
            self._doc_context = DocContext(
                task_id=task_id,
                title=title,
                session_id=session_id,
            )

        ctx = self._doc_context

        try:
            result = await self._run_phase_engine(ctx)

            if result.error:
                self.log.error(
                    "Error in doc phase",
                    phase=ctx.phase.value,
                    error=result.error,
                )
                return False

            if result.completed:
                self._doc_context = None
                return True

            return False

        except Exception as e:
            self.log.error(
                "Error in doc phase",
                phase=ctx.phase.value,
                error=str(e),
            )
            return False

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_receive(self, ctx: DocContext) -> None:
        """
        RECEIVE phase: Claim documentation task.
        """
        self.log.info("RECEIVE phase", task_id=str(ctx.task_id))

        # CLAIM: Transition from awaiting_documentation to claimed
        await self._mark_claimed(ctx.task_id)

        await self.send_message(
            ctx.session_id,
            f"Starting documentation for TASK-{str(ctx.task_id)[:8]}: {ctx.title}",
            message_type="action",
            task_id=ctx.task_id,
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
                    path="docs/backend/api/",
                    priority="required",
                )
            )

        # Add component docs if frontend
        if self.team == Team.FRONTEND:
            ctx.documents_needed.append(
                DocumentSpec(
                    doc_type=DocType.COMPONENT,
                    title=f"Component documentation for {ctx.title}",
                    path="docs/frontend/components/",
                    priority="required",
                )
            )

        # PLAN: Save documentation plan to task API (required before start)
        plan_data = {
            "approach": f"Document {ctx.title}",
            "sub_tasks": [
                {
                    "id": f"doc-{i}",
                    "title": doc.title,
                    "description": f"Write {doc.doc_type.value} at {doc.path}",
                    "completed": False,
                    "order": i,
                }
                for i, doc in enumerate(ctx.documents_needed)
            ],
            "risks": [],
        }
        await self._api_call("PATCH", f"/tasks/{ctx.task_id}", json={"plan": plan_data})

        ctx.notes.append(
            f"[{datetime.now(UTC).isoformat()}] Synthesis complete: "
            f"{len(ctx.documents_needed)} documents needed"
        )

    async def _phase_write(self, ctx: DocContext) -> bool:
        """
        WRITE phase: Create/update documentation.

        - START: Transition to in_progress on first doc
        - Write each document

        Returns True when all docs written.
        """
        self.log.info(
            "WRITE phase",
            task_id=str(ctx.task_id),
            doc=ctx.current_doc,
            total=len(ctx.documents_needed),
        )

        # START: Transition to in_progress on first doc
        if ctx.current_doc == 0:
            await self._mark_in_progress(ctx.task_id)
            self.log.info(
                "Documentation started (in_progress)", task_id=str(ctx.task_id)
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
            ctx.session_id,
            f"TASK-{str(ctx.task_id)[:8]} doc {progress}: {doc_spec.title}",
            message_type="action",
            task_id=ctx.task_id,
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

        # Use proper docs-complete endpoint (handles notes, status)
        doc_summary = f"Published: {', '.join(ctx.written_docs)}"
        await self._docs_complete(ctx.task_id, doc_summary)

        await self.send_message(
            ctx.session_id,
            f"TASK-{str(ctx.task_id)[:8]} documentation complete, awaiting PM review\n"
            f"{doc_summary}",
            message_type="action",
            task_id=ctx.task_id,
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

    async def _read_qa_feedback(self, task_id: UUID) -> str:
        """Read QA feedback."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            feedback: str = result.get("qa_feedback", "No QA feedback available")
            return feedback
        except Exception as e:
            self.log.warning("Failed to read QA feedback", error=str(e))
            return "QA feedback unavailable"

    async def _get_conversations(self, task_id: UUID) -> list[str]:
        """Get relevant conversations."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}/messages")
            messages: list[dict[str, str]] = result.get("items", [])
            return [m.get("content", "") for m in messages]
        except Exception as e:
            self.log.warning("Failed to get conversations", error=str(e))
            return []

    async def _get_code_changes(self, task_id: UUID) -> list[str]:
        """Get code changes from commits."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            changes: list[str] = result.get("code_changes", [])
            return changes
        except Exception as e:
            self.log.warning("Failed to get code changes", error=str(e))
            return []
