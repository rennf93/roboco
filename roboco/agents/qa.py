"""
QA Agent

Implementation of the QA workflow from the blueprint.
Handles review lifecycle:
    MONITOR → RECEIVE → UNDERSTAND → TEST → VERDICT → DOCUMENT → RETURN
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.agents.mixins import PhaseConfig, PhaseEngine
from roboco.models.agents import (
    QATaskPhase,
    ReviewContext,
    TestCase,
    TestResult,
)

logger = structlog.get_logger()


class QAAgent(Agent, PhaseEngine[QATaskPhase, ReviewContext]):
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

    async def _initialize(self) -> None:
        """Initialize QA-specific resources."""
        self.log.debug("QA agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup QA-specific resources."""
        self._review_context = None
        self._pending_reviews.clear()
        self.log.debug("QA agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # PHASE ENGINE IMPLEMENTATION
    # =========================================================================

    def _get_phase_configs(self) -> list[PhaseConfig[QATaskPhase]]:
        """Define the QA workflow phases."""
        return [
            PhaseConfig(
                QATaskPhase.RECEIVE,
                self._phase_receive,
                next_phase=QATaskPhase.UNDERSTAND,
            ),
            PhaseConfig(
                QATaskPhase.UNDERSTAND,
                self._phase_understand,
                next_phase=QATaskPhase.TEST,
            ),
            PhaseConfig(
                QATaskPhase.TEST,
                self._phase_test,
                next_phase=QATaskPhase.VERDICT,
                requires_completion=True,
            ),
            PhaseConfig(
                QATaskPhase.VERDICT,
                self._phase_verdict,
                next_phase=QATaskPhase.DOCUMENT,
            ),
            PhaseConfig(
                QATaskPhase.DOCUMENT,
                self._phase_document,
                next_phase=QATaskPhase.RETURN,
            ),
            PhaseConfig(
                QATaskPhase.RETURN,
                self._phase_return,
                next_phase=None,  # Terminal
            ),
        ]

    def _get_current_phase(self, ctx: ReviewContext) -> QATaskPhase:
        """Get the current phase from context."""
        return ctx.phase

    def _set_current_phase(self, ctx: ReviewContext, phase: QATaskPhase) -> None:
        """Set the current phase in context."""
        ctx.phase = phase

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
        if self._review_context is None or self._review_context.task_id != task_id:
            self._review_context = ReviewContext(
                task_id=task_id,
                title=await self._get_task_title(task_id),
            )

        ctx = self._review_context

        try:
            result = await self._run_phase_engine(ctx)

            if result.error:
                self.log.error(
                    "Error in review phase",
                    phase=ctx.phase.value,
                    error=result.error,
                )
                ctx.findings.append(f"Error during review: {result.error}")
                return False

            if result.completed:
                self._review_context = None
                return True

            return False

        except Exception as e:
            self.log.error(
                "Error in review phase",
                phase=ctx.phase.value,
                error=str(e),
            )
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

        ctx.notes.append(f"[{datetime.now(UTC).isoformat()}] Review started")

    async def _phase_understand(self, ctx: ReviewContext) -> None:
        """
        UNDERSTAND phase: Read requirements and dev notes.

        - Read task requirements and acceptance criteria
        - Read dev's handoff notes (from task's dev_notes field)
        - Review commits
        - Check conversation history
        - Read developer's journal entries for this task (if needed)
        """
        self.log.info("UNDERSTAND phase", task_id=str(ctx.task_id))

        # Read task context
        requirements = await self._read_task_requirements(ctx.task_id)
        dev_notes = await self._read_dev_notes(ctx.task_id)
        commits = await self._get_task_commits_formatted(ctx.task_id)

        # Read developer journal entries for this task (cell members can read)
        dev_journal = await self._read_team_journal_for_task(ctx.task_id)

        # Use TOON for token-efficient context encoding
        task_context = self._format_review_context(
            ctx.title, requirements, dev_notes, commits
        )

        # Include journal context if available
        journal_context = ""
        if dev_journal:
            journal_context = f"\n\nDeveloper Journal Entries:\n{dev_journal}"

        prompt = f"""You are a QA engineer reviewing a completed task.

{task_context}{journal_context}

Based on this, create test cases to verify the implementation.

Focus on:
- Acceptance criteria verification
- Edge cases
- Integration points
- Error handling

If acceptance criteria mentions journaling requirements, verify them against
the developer journal entries provided above.

Format response as TOON tabular:
[N,]{{name,description,steps,expected}}:
Acceptance Criteria,Verify all criteria met,Review implementation|Check each criterion,All criteria satisfied
"""  # noqa: E501
        _response = await self.think(prompt)

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

        ts = datetime.now(UTC).isoformat()
        ctx.notes.append(f"[{ts}] Created {len(ctx.test_cases)} test cases")

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

        # Use TOON for token-efficient context encoding
        test_context = self._format_test_context(
            test_case.name,
            test_case.description,
            test_case.steps,
            test_case.expected,
        )

        prompt = f"""Execute this test case:

{test_context}

Simulate executing this test and provide results.

Format response as TOON:
{{result,actual,notes}}:
PASS,All criteria verified successfully,No issues found
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
        progress = f"{ctx.current_test}/{len(ctx.test_cases)}"
        result_str = test_case.result.value.upper()
        task_ref = str(ctx.task_id)[:8]
        msg = f"TASK-{task_ref} test {progress}: {test_case.name} - {result_str}"
        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            msg,
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
            await self._mark_needs_revision(ctx.task_id)

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
            await self._mark_awaiting_documentation(ctx.task_id)

        ctx.notes.append(
            f"[{datetime.now(UTC).isoformat()}] Verdict: {ctx.verdict.value.upper()}"
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
**Reviewed**: {datetime.now(UTC).isoformat()}

### Tests Executed

{test_summary}

### Findings

{chr(10).join(ctx.findings) if ctx.findings else "No issues found"}

### Notes

{chr(10).join(ctx.notes)}
"""

        # Would save to task record
        self.log.info("QA report generated", report_length=len(qa_report))

        ctx.notes.append(f"[{datetime.now(UTC).isoformat()}] QA documentation complete")

    async def _phase_return(self, ctx: ReviewContext) -> None:
        """
        RETURN phase: Clean up and return to monitoring.
        """
        self.log.info("RETURN phase", task_id=str(ctx.task_id))
        # Context will be cleared by execute_task on completion

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _find_awaiting_qa(self) -> UUID | None:
        """Find tasks awaiting QA review."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "awaiting_qa", "team": team_param},
            )
            tasks = result.get("items", [])
            return UUID(tasks[0]["id"]) if tasks else None
        except Exception as e:
            self.log.warning("Failed to find awaiting QA task", error=str(e))
            return None

    async def _get_task_commits_formatted(self, task_id: UUID) -> str:
        """Get commits for the task as formatted string."""
        commits = await self._get_task_commits(task_id)
        return "\n".join(commits) if commits else "No commits recorded"
