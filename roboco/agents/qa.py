"""
QA Agent

Implementation of the QA workflow from the blueprint.
Handles review lifecycle: MONITOR → RECEIVE → UNDERSTAND → TEST → VERDICT → DOCUMENT → RETURN
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, TaskStatus, Team

logger = structlog.get_logger()


class QATaskPhase(str, Enum):
    """Phases of the QA lifecycle."""

    MONITOR = "monitor"
    RECEIVE = "receive"
    UNDERSTAND = "understand"
    TEST = "test"
    VERDICT = "verdict"
    DOCUMENT = "document"
    RETURN = "return"


class TestResult(str, Enum):
    """Test result outcomes."""

    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass
class TestCase:
    """A single test case."""

    name: str
    description: str
    steps: list[str]
    expected: str
    result: TestResult | None = None
    actual: str | None = None
    notes: str | None = None


@dataclass
class ReviewContext:
    """Context for the current review being conducted."""

    task_id: UUID
    title: str
    phase: QATaskPhase = QATaskPhase.RECEIVE
    test_cases: list[TestCase] = field(default_factory=list)
    current_test: int = 0
    findings: list[str] = field(default_factory=list)
    verdict: TestResult | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    notes: list[str] = field(default_factory=list)


class QAAgent(Agent):
    """
    QA agent that follows the QA Lifecycle.

    Workflow:
    1. MONITOR - Watch cell channel, track tasks approaching completion
    2. RECEIVE - Dev flags ready, claim review task
    3. UNDERSTAND - Read requirements, dev notes, commits
    4. TEST - Execute test scenarios, edge cases
    5. VERDICT - PASS or FAIL with clear feedback
    6. DOCUMENT - Add QA notes, test coverage
    7. RETURN - Back to monitoring
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize QA agent."""
        super().__init__(config)
        self._review_context: ReviewContext | None = None
        self._cell_channel_id: UUID | None = None
        self._pending_reviews: list[UUID] = []

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
        MONITOR phase: Watch for tasks ready for review.

        - Check for tasks flagged as awaiting_qa
        - Check for PM notifications
        """
        self.log.info("Monitoring for reviews")

        # Check pending reviews queue
        if self._pending_reviews:
            return self._pending_reviews.pop(0)

        # Query for tasks awaiting QA
        task_id = await self._find_awaiting_qa()
        if task_id:
            return task_id

        return None

    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute review through QA lifecycle phases.

        Returns True when review is complete.
        """
        # Initialize or restore review context
        if self._review_context is None or self._review_context.task_id != task_id:
            self._review_context = ReviewContext(
                task_id=task_id,
                title=await self._get_task_title(task_id),
            )

        ctx = self._review_context

        try:
            match ctx.phase:
                case QATaskPhase.RECEIVE:
                    await self._phase_receive(ctx)
                    ctx.phase = QATaskPhase.UNDERSTAND

                case QATaskPhase.UNDERSTAND:
                    await self._phase_understand(ctx)
                    ctx.phase = QATaskPhase.TEST

                case QATaskPhase.TEST:
                    completed = await self._phase_test(ctx)
                    if completed:
                        ctx.phase = QATaskPhase.VERDICT

                case QATaskPhase.VERDICT:
                    await self._phase_verdict(ctx)
                    ctx.phase = QATaskPhase.DOCUMENT

                case QATaskPhase.DOCUMENT:
                    await self._phase_document(ctx)
                    ctx.phase = QATaskPhase.RETURN

                case QATaskPhase.RETURN:
                    self._review_context = None
                    return True

            return False

        except Exception as e:
            self.log.error("Error in review phase", phase=ctx.phase.value, error=str(e))
            ctx.findings.append(f"Error during review: {e}")
            return False

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_receive(self, ctx: ReviewContext) -> None:
        """
        RECEIVE phase: Claim the review task.

        - Acknowledge receipt
        - Announce review started
        """
        self.log.info("RECEIVE phase", task_id=str(ctx.task_id))

        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"Starting review of TASK-{str(ctx.task_id)[:8]}: {ctx.title}",
            message_type="action",
        )

        ctx.notes.append(f"[{datetime.utcnow().isoformat()}] Review started")

    async def _phase_understand(self, ctx: ReviewContext) -> None:
        """
        UNDERSTAND phase: Read requirements and dev notes.

        - Read task requirements and acceptance criteria
        - Read dev's journey notes
        - Review commits
        - Check conversation history
        """
        self.log.info("UNDERSTAND phase", task_id=str(ctx.task_id))

        # Read task context
        requirements = await self._read_task_requirements(ctx.task_id)
        dev_notes = await self._read_dev_notes(ctx.task_id)
        commits = await self._get_task_commits(ctx.task_id)

        # Use LLM to understand and create test plan
        prompt = f"""
You are a QA engineer reviewing a completed task.

Task: {ctx.title}

Requirements:
{requirements}

Developer Notes:
{dev_notes}

Commits:
{commits}

Based on this, create test cases to verify the implementation.
For each test case provide:
1. Name
2. What to test
3. Steps to execute
4. Expected outcome

Focus on:
- Acceptance criteria verification
- Edge cases
- Integration points
- Error handling

Format as JSON array.
"""
        response = await self.think(prompt)

        # Create test cases (simplified parsing)
        ctx.test_cases = [
            TestCase(
                name="Acceptance Criteria",
                description="Verify all acceptance criteria are met",
                steps=["Review implementation", "Check each criterion"],
                expected="All criteria satisfied",
            ),
            TestCase(
                name="Edge Cases",
                description="Test edge cases and error handling",
                steps=["Test with invalid input", "Test boundary conditions"],
                expected="Graceful handling of edge cases",
            ),
            TestCase(
                name="Integration",
                description="Verify integration with existing code",
                steps=["Run integration tests", "Check API compatibility"],
                expected="No breaking changes",
            ),
        ]

        ctx.notes.append(
            f"[{datetime.utcnow().isoformat()}] Created {len(ctx.test_cases)} test cases"
        )

    async def _phase_test(self, ctx: ReviewContext) -> bool:
        """
        TEST phase: Execute test scenarios.

        - Run through each test case
        - Document findings

        Returns True when all tests complete.
        """
        self.log.info(
            "TEST phase",
            task_id=str(ctx.task_id),
            test=ctx.current_test,
            total=len(ctx.test_cases),
        )

        if ctx.current_test >= len(ctx.test_cases):
            return True

        test_case = ctx.test_cases[ctx.current_test]

        # Use LLM to execute test
        prompt = f"""
Execute this test case:

Test: {test_case.name}
Description: {test_case.description}
Steps: {", ".join(test_case.steps)}
Expected: {test_case.expected}

Simulate executing this test and provide:
1. RESULT: PASS or FAIL
2. ACTUAL: What was observed
3. NOTES: Any additional findings

Format:
RESULT: [PASS|FAIL]
ACTUAL: [observation]
NOTES: [notes]
"""
        response = await self.think(prompt)

        # Parse result (simplified)
        if "PASS" in response.upper():
            test_case.result = TestResult.PASS
        else:
            test_case.result = TestResult.FAIL
            ctx.findings.append(f"FAIL: {test_case.name}")

        test_case.actual = response
        ctx.current_test += 1

        # Progress update
        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} test {ctx.current_test}/{len(ctx.test_cases)}: "
            f"{test_case.name} - {test_case.result.value.upper()}",
            message_type="action",
        )

        return ctx.current_test >= len(ctx.test_cases)

    async def _phase_verdict(self, ctx: ReviewContext) -> None:
        """
        VERDICT phase: Determine overall pass/fail.

        - Analyze all test results
        - Communicate clear verdict
        - If fail, provide specific feedback
        """
        self.log.info("VERDICT phase", task_id=str(ctx.task_id))

        # Determine verdict
        failed_tests = [t for t in ctx.test_cases if t.result == TestResult.FAIL]

        if failed_tests:
            ctx.verdict = TestResult.FAIL

            # Communicate failure with specifics
            failure_summary = "\n".join(
                [f"- {t.name}: {t.actual or 'No details'}" for t in failed_tests]
            )

            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"TASK-{str(ctx.task_id)[:8]} QA FAILED\n\n"
                f"Issues found:\n{failure_summary}\n\n"
                f"Task returned to developer for fixes.",
                message_type="decision",
            )

            # Update task status
            await self._update_task_status(ctx.task_id, TaskStatus.NEEDS_REVISION)

        else:
            ctx.verdict = TestResult.PASS

            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"TASK-{str(ctx.task_id)[:8]} QA APPROVED\n\n"
                f"All {len(ctx.test_cases)} tests passed.\n"
                f"Ready for documentation.",
                message_type="decision",
            )

            # Update task status
            await self._update_task_status(
                ctx.task_id, TaskStatus.AWAITING_DOCUMENTATION
            )

        ctx.notes.append(
            f"[{datetime.utcnow().isoformat()}] Verdict: {ctx.verdict.value.upper()}"
        )

    async def _phase_document(self, ctx: ReviewContext) -> None:
        """
        DOCUMENT phase: Add QA notes to task.

        - Document test coverage
        - Add handoff notes for documenter
        """
        self.log.info("DOCUMENT phase", task_id=str(ctx.task_id))

        # Generate QA report
        test_summary = "\n".join(
            [
                f"- {t.name}: {t.result.value.upper() if t.result else 'NOT RUN'}"
                for t in ctx.test_cases
            ]
        )

        qa_report = f"""
## QA Review Summary

**Task**: {ctx.title}
**Verdict**: {ctx.verdict.value.upper() if ctx.verdict else "UNKNOWN"}
**Reviewed**: {datetime.utcnow().isoformat()}

### Tests Executed

{test_summary}

### Findings

{chr(10).join(ctx.findings) if ctx.findings else "No issues found"}

### Notes

{chr(10).join(ctx.notes)}
"""

        # Would save to task record
        self.log.info("QA report generated", report_length=len(qa_report))

        ctx.notes.append(f"[{datetime.utcnow().isoformat()}] QA documentation complete")

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _find_awaiting_qa(self) -> UUID | None:
        """Find tasks awaiting QA review."""
        # TODO: Query task API
        return None

    async def _get_task_title(self, task_id: UUID) -> str:
        """Get task title."""
        # TODO: Query task API
        return f"Task {str(task_id)[:8]}"

    async def _read_task_requirements(self, task_id: UUID) -> str:
        """Read task requirements."""
        # TODO: Read from task record
        return "Requirements placeholder"

    async def _read_dev_notes(self, task_id: UUID) -> str:
        """Read developer's journey notes."""
        # TODO: Read from task record
        return "Dev notes placeholder"

    async def _get_task_commits(self, task_id: UUID) -> str:
        """Get commits for the task."""
        # TODO: Read from task record
        return "Commits placeholder"

    async def _update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        """Update task status."""
        # TODO: Update via task API
        self.log.info("Task status updated", task_id=str(task_id), status=status.value)


def create_backend_qa(
    name: str = "BE-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a backend QA agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/backend/be-qa.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a backend QA engineer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.QA,
        team=Team.BACKEND,
        system_prompt=system_prompt,
        capabilities=["code_review", "test_execution", "security_analysis"],
    )

    return QAAgent(config)


def create_frontend_qa(
    name: str = "FE-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a frontend QA agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/frontend/fe-qa.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a frontend QA engineer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.QA,
        team=Team.FRONTEND,
        system_prompt=system_prompt,
        capabilities=["visual_testing", "a11y_testing", "browser_testing"],
    )

    return QAAgent(config)


def create_ux_qa(
    name: str = "UX-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a UX/UI QA agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/ux_ui/ux-qa.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a UX/UI QA engineer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.QA,
        team=Team.UX_UI,
        system_prompt=system_prompt,
        capabilities=["design_review", "consistency_check", "a11y_testing"],
    )

    return QAAgent(config)
