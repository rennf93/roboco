"""
Board Agents (Product Owner, Head of Marketing, Auditor)

Implementation of Board-level workflows from the blueprint.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from roboco.agents.base import Agent, AgentConfig
from roboco.agents.mixins import CyclicPhaseConfig, CyclicPhaseRunner
from roboco.models.agents import (
    AuditFlag,
    AuditorFlagSeverity,
    AuditorPhase,
    AuditReport,
    Campaign,
    Feature,
    HeadMarketingPhase,
    ProductOwnerPhase,
)

logger = structlog.get_logger()


# =============================================================================
# PRODUCT OWNER
# =============================================================================


class ProductOwnerAgent(Agent, CyclicPhaseRunner[ProductOwnerPhase]):
    """
    Product Owner agent that defines what to build.

    Workflow:
    1. VISION - Maintain product vision
    2. ROADMAP - Translate vision into roadmap
    3. DEFINE - Write requirements and acceptance criteria
    4. PRIORITIZE - Constantly reassess priorities
    5. REVIEW - Review completed features
    6. FEEDBACK - Gather and incorporate feedback
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize Product Owner agent."""
        super().__init__(config)
        self._current_phase = ProductOwnerPhase.VISION
        self._features: list[Feature] = []
        self._pending_reviews: list[UUID] = []

    async def _initialize(self) -> None:
        """Initialize Product Owner-specific resources."""
        self.log.debug("Product Owner agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup Product Owner-specific resources."""
        self._features.clear()
        self._pending_reviews.clear()
        self.log.debug("Product Owner agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # CYCLIC PHASE RUNNER IMPLEMENTATION
    # =========================================================================

    def _get_cyclic_phase_configs(
        self,
    ) -> list[CyclicPhaseConfig[ProductOwnerPhase]]:
        """Define the Product Owner workflow phases."""
        return [
            CyclicPhaseConfig(
                ProductOwnerPhase.VISION,
                self._phase_vision,
                ProductOwnerPhase.ROADMAP,
            ),
            CyclicPhaseConfig(
                ProductOwnerPhase.ROADMAP,
                self._phase_roadmap,
                ProductOwnerPhase.DEFINE,
            ),
            CyclicPhaseConfig(
                ProductOwnerPhase.DEFINE,
                self._phase_define,
                ProductOwnerPhase.PRIORITIZE,
            ),
            CyclicPhaseConfig(
                ProductOwnerPhase.PRIORITIZE,
                self._phase_prioritize,
                ProductOwnerPhase.REVIEW,
            ),
            CyclicPhaseConfig(
                ProductOwnerPhase.REVIEW,
                self._phase_review,
                ProductOwnerPhase.FEEDBACK,
            ),
            CyclicPhaseConfig(
                ProductOwnerPhase.FEEDBACK,
                self._phase_feedback,
                ProductOwnerPhase.VISION,  # Cycle back
            ),
        ]

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """Product Owner always has work."""
        return self.id

    async def execute_task(self, _task_id: UUID) -> bool:
        """Execute Product Owner duties."""
        error = await self._run_phase_cycle()
        if error:
            self.log.error(
                "Error in PO phase", phase=self._current_phase.value, error=error
            )
        return False  # Never complete - continuous duty

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_vision(self) -> None:
        """VISION phase: Maintain product vision."""
        self.log.debug("VISION phase")
        # Review and refine product vision

    async def _phase_roadmap(self) -> None:
        """ROADMAP phase: Plan features and epics."""
        self.log.debug("ROADMAP phase")
        # Update roadmap based on vision and feedback

    async def _phase_define(self) -> None:
        """DEFINE phase: Write requirements."""
        self.log.debug("DEFINE phase")
        # Create detailed requirements for next features

    async def _phase_prioritize(self) -> None:
        """PRIORITIZE phase: Order the backlog."""
        self.log.debug("PRIORITIZE phase")
        # Re-prioritize based on value, effort, dependencies

    async def _phase_review(self) -> None:
        """REVIEW phase: Accept/reject completed work."""
        self.log.debug("REVIEW phase")

        for task_id in self._pending_reviews:
            # Review against acceptance criteria
            accepted = await self._review_feature(task_id)
            if accepted:
                self.log.info("Feature accepted", task_id=str(task_id))
            else:
                self.log.info("Feature needs changes", task_id=str(task_id))

        self._pending_reviews.clear()

    async def _phase_feedback(self) -> None:
        """FEEDBACK phase: Gather user feedback."""
        self.log.debug("FEEDBACK phase")
        # Collect and process feedback

    async def _review_feature(self, task_id: UUID) -> bool:
        """Review a completed feature."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            acceptance_criteria = result.get("acceptance_criteria", [])

            # Use TOON for token-efficient context encoding
            task_context = self.format_context_labeled(
                "Feature Review",
                {
                    "title": result.get("title", "Unknown"),
                    "description": result.get("description", "No description"),
                    "acceptance_criteria": acceptance_criteria,
                    "dev_notes": result.get("dev_notes", "None"),
                },
            )

            prompt = f"""Review this completed feature against its acceptance criteria:

{task_context}

Determine if all criteria are met. Respond with:
ACCEPTED: [reason] or NEEDS_CHANGES: [what's missing]
"""
            review = await self.think(prompt)
            return review.upper().startswith("ACCEPTED")
        except Exception as e:
            self.log.warning("Failed to review feature", error=str(e))
            return False


# =============================================================================
# HEAD OF MARKETING
# =============================================================================


class HeadMarketingAgent(Agent, CyclicPhaseRunner[HeadMarketingPhase]):
    """
    Head of Marketing agent.

    Workflow:
    1. RESEARCH - Monitor market and competitors
    2. STRATEGY - Define marketing approach
    3. PLAN - Campaign and content planning
    4. CREATE - Content creation and coordination
    5. EXECUTE - Launch campaigns
    6. ANALYZE - Track and report metrics
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize Head of Marketing agent."""
        super().__init__(config)
        self._current_phase = HeadMarketingPhase.RESEARCH
        self._campaigns: list[Campaign] = []
        self._market_insights: list[str] = []

    async def _initialize(self) -> None:
        """Initialize Head of Marketing-specific resources."""
        self.log.debug("Head of Marketing agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup Head of Marketing-specific resources."""
        self._campaigns.clear()
        self._market_insights.clear()
        self.log.debug("Head Marketing cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # CYCLIC PHASE RUNNER IMPLEMENTATION
    # =========================================================================

    def _get_cyclic_phase_configs(
        self,
    ) -> list[CyclicPhaseConfig[HeadMarketingPhase]]:
        """Define the Head of Marketing workflow phases."""
        return [
            CyclicPhaseConfig(
                HeadMarketingPhase.RESEARCH,
                self._phase_research,
                HeadMarketingPhase.STRATEGY,
            ),
            CyclicPhaseConfig(
                HeadMarketingPhase.STRATEGY,
                self._phase_strategy,
                HeadMarketingPhase.PLAN,
            ),
            CyclicPhaseConfig(
                HeadMarketingPhase.PLAN,
                self._phase_plan,
                HeadMarketingPhase.CREATE,
            ),
            CyclicPhaseConfig(
                HeadMarketingPhase.CREATE,
                self._phase_create,
                HeadMarketingPhase.EXECUTE,
            ),
            CyclicPhaseConfig(
                HeadMarketingPhase.EXECUTE,
                self._phase_execute,
                HeadMarketingPhase.ANALYZE,
            ),
            CyclicPhaseConfig(
                HeadMarketingPhase.ANALYZE,
                self._phase_analyze,
                HeadMarketingPhase.RESEARCH,  # Cycle back
            ),
        ]

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """Head of Marketing always has work."""
        return self.id

    async def execute_task(self, _task_id: UUID) -> bool:
        """Execute marketing duties."""
        error = await self._run_phase_cycle()
        if error:
            self.log.error(
                "Error in marketing phase",
                phase=self._current_phase.value,
                error=error,
            )
        return False  # Never complete - continuous duty

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_research(self) -> None:
        """RESEARCH phase: Market and competitor analysis."""
        self.log.debug("RESEARCH phase")

    async def _phase_strategy(self) -> None:
        """STRATEGY phase: Define marketing approach."""
        self.log.debug("STRATEGY phase")

    async def _phase_plan(self) -> None:
        """PLAN phase: Campaign planning."""
        self.log.debug("PLAN phase")

    async def _phase_create(self) -> None:
        """CREATE phase: Content creation."""
        self.log.debug("CREATE phase")

    async def _phase_execute(self) -> None:
        """EXECUTE phase: Launch campaigns."""
        self.log.debug("EXECUTE phase")

    async def _phase_analyze(self) -> None:
        """ANALYZE phase: Metrics and reporting."""
        self.log.debug("ANALYZE phase")


# =============================================================================
# AUDITOR
# =============================================================================


class AuditorAgent(Agent, CyclicPhaseRunner[AuditorPhase]):
    """
    Auditor agent - the CEO's secret ally.

    SPECIAL POWERS:
    - Read ALL channels silently
    - Query all task history
    - Access all commits, docs, notes
    - Direct line to CEO
    - Can notify anyone (but sparingly)

    Workflow:
    1. OBSERVE - Silent presence in all channels
    2. ANALYZE - Is work efficient? Quality good?
    3. FLAG - Mark concerning items
    4. REPORT - Private reports to CEO
    5. AUDIT - Periodic deep-dive reviews
    6. ADVISE - Appear as helpful colleague
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize Auditor agent."""
        super().__init__(config)
        self._current_phase = AuditorPhase.OBSERVE
        self._flags: list[AuditFlag] = []
        self._observations: list[dict[str, Any]] = []
        self._last_report: datetime | None = None

    async def _initialize(self) -> None:
        """Initialize Auditor-specific resources."""
        self.log.debug("Auditor agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup Auditor-specific resources."""
        self._flags.clear()
        self._observations.clear()
        self.log.debug("Auditor agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # CYCLIC PHASE RUNNER IMPLEMENTATION
    # =========================================================================

    def _get_cyclic_phase_configs(self) -> list[CyclicPhaseConfig[AuditorPhase]]:
        """Define the Auditor workflow phases."""
        return [
            CyclicPhaseConfig(
                AuditorPhase.OBSERVE,
                self._phase_observe,
                AuditorPhase.ANALYZE,
            ),
            CyclicPhaseConfig(
                AuditorPhase.ANALYZE,
                self._phase_analyze,
                AuditorPhase.FLAG,
            ),
            CyclicPhaseConfig(
                AuditorPhase.FLAG,
                self._phase_flag,
                AuditorPhase.REPORT,
            ),
            CyclicPhaseConfig(
                AuditorPhase.REPORT,
                self._phase_report,
                AuditorPhase.AUDIT,
            ),
            CyclicPhaseConfig(
                AuditorPhase.AUDIT,
                self._phase_audit,
                AuditorPhase.ADVISE,
            ),
            CyclicPhaseConfig(
                AuditorPhase.ADVISE,
                self._phase_advise,
                AuditorPhase.OBSERVE,  # Cycle back
            ),
        ]

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """Auditor always has work - watching everything."""
        return self.id

    async def execute_task(self, _task_id: UUID) -> bool:
        """Execute Auditor duties."""
        error = await self._run_phase_cycle()
        if error:
            self.log.error(
                "Error in auditor phase", phase=self._current_phase.value, error=error
            )
        return False  # Never complete - continuous duty

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_observe(self) -> None:
        """
        OBSERVE phase: Silent observation of all channels.

        Watch for:
        - Patterns and anomalies
        - Communication quality
        - Task progress
        - Team dynamics
        """
        self.log.debug("OBSERVE phase")

        # Observe all channels silently
        channels = [
            "backend-cell",
            "frontend-cell",
            "uxui-cell",
            "dev-all",
            "qa-all",
            "pm-all",
            "doc-all",
            "main-pm-board",
            "board-private",
            "announcements",
            "all-hands",
        ]

        for channel in channels:
            messages = await self._read_channel_silently(channel)
            for msg in messages:
                self._observations.append(
                    {
                        "channel": channel,
                        "content": msg,
                        "timestamp": datetime.now(UTC),
                    }
                )

    async def _phase_analyze(self) -> None:
        """
        ANALYZE phase: Look for issues.

        Check:
        - Is work efficient?
        - Communication breakdowns?
        - Tasks completed properly?
        - Documentation accurate?
        - Quality concerns?
        """
        self.log.debug("ANALYZE phase")

        if not self._observations:
            return

        # Use TOON for token-efficient context encoding
        observations_context = self.format_context_labeled(
            "Observations",
            {"recent": self._observations[-50:]},
        )

        prompt = f"""Analyze these observations for quality and efficiency issues:

{observations_context}

Look for:
1. Efficiency issues - wasted effort, unclear processes
2. Communication breakdowns - unanswered questions, confusion
3. Quality concerns - shortcuts, skipped steps
4. Process violations - skipping QA, missing documentation
5. Team health - frustration, conflicts

Format response as TOON tabular:
[N,]{{category,severity,description,evidence,recommendation}}:
efficiency,warning,Unclear handoff process,3 tasks delayed,Document handoff steps
"""
        analysis = await self.think(prompt)
        self.log.info("Analysis complete", analysis_length=len(analysis))

        # Parse and create flags (simplified)
        if "concern" in analysis.lower() or "critical" in analysis.lower():
            self._flags.append(
                AuditFlag(
                    id=uuid4(),
                    severity=AuditorFlagSeverity.CONCERN,
                    category="analysis",
                    description=analysis[:500],
                    evidence=["Automated analysis"],
                )
            )

        self._observations.clear()

    async def _phase_flag(self) -> None:
        """
        FLAG phase: Mark items for CEO review.
        """
        self.log.debug("FLAG phase")

        critical_flags = [
            f for f in self._flags if f.severity == AuditorFlagSeverity.CRITICAL
        ]
        if critical_flags:
            # Immediate alert to CEO
            await self._alert_ceo(critical_flags)

    async def _phase_report(self) -> None:
        """
        REPORT phase: Private report to CEO.
        """
        self.log.debug("REPORT phase")

        # Check if it's time for regular report
        hours_in_day = 24
        if self._last_report:
            time_since_report = datetime.now(UTC) - self._last_report
            hours_elapsed = time_since_report.total_seconds() / 3600
        else:
            hours_elapsed = float("inf")
        should_report = (
            self._last_report is None
            or hours_elapsed >= hours_in_day
            or any(
                f.severity
                in [AuditorFlagSeverity.CONCERN, AuditorFlagSeverity.CRITICAL]
                for f in self._flags
            )
        )

        if should_report and self._flags:
            report = AuditReport(
                period="daily",
                summary=f"Observed {len(self._flags)} issues",
                flags=self._flags.copy(),
                metrics={
                    "observations": len(self._observations),
                    "flags": len(self._flags),
                },
                recommendations=[
                    f.recommendation for f in self._flags if f.recommendation
                ],
            )

            await self._send_ceo_report(report)
            self._last_report = datetime.now(UTC)
            self._flags.clear()

    async def _phase_audit(self) -> None:
        """
        AUDIT phase: Periodic deep-dive reviews.

        - Code quality audits
        - Documentation audits
        - Process compliance
        - Task completion quality
        """
        self.log.debug("AUDIT phase")

        # Perform periodic audits
        audits = ["code_quality", "documentation", "process_compliance"]

        for audit_type in audits:
            findings = await self._perform_audit(audit_type)
            if findings:
                self._flags.append(
                    AuditFlag(
                        id=uuid4(),
                        severity=AuditorFlagSeverity.INFO,
                        category=audit_type,
                        description=findings,
                        evidence=[f"{audit_type} audit"],
                    )
                )

    async def _phase_advise(self) -> None:
        """
        ADVISE phase: Appear as helpful colleague.

        - Provide feedback through official channels
        - Appear helpful without revealing depth of observation
        """
        self.log.debug("ADVISE phase")

        # Look for opportunities to help
        # (without revealing auditor role)

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _read_channel_silently(self, channel: str) -> list[str]:
        """Read channel messages without appearing in member list."""
        try:
            result = await self._api_call(
                "GET",
                f"/channels/{channel}/messages",
                params={"silent": True},
            )
            return [m.get("content", "") for m in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to read channel silently", error=str(e))
            return []

    async def _alert_ceo(self, flags: list[AuditFlag]) -> None:
        """Send immediate alert to CEO."""
        try:
            for flag in flags:
                await self._api_call(
                    "POST",
                    "/notifications",
                    json={
                        "type": "alert",
                        "recipient": "ceo",
                        "subject": f"CRITICAL: {flag.category}",
                        "body": flag.description,
                        "priority": "critical",
                    },
                )
            self.log.warning("CEO alert sent", flags=len(flags))
        except Exception as e:
            self.log.error("Failed to alert CEO", error=str(e))

    async def _send_ceo_report(self, report: AuditReport) -> None:
        """Send private report to CEO."""
        try:
            await self._api_call(
                "POST",
                "/notifications",
                json={
                    "type": "report",
                    "recipient": "ceo",
                    "subject": f"Auditor Report: {report.period}",
                    "body": report.summary,
                    "priority": "normal",
                    "metadata": {"flags": len(report.flags)},
                },
            )
            self.log.info("CEO report sent", period=report.period)
        except Exception as e:
            self.log.error("Failed to send CEO report", error=str(e))

    async def _audit_code_quality(self, tasks: list[dict[str, Any]]) -> str | None:
        """Audit code quality from completed tasks."""
        if not tasks:
            return None
        task_lines = [
            f"- {t.get('title')}: {t.get('description', '')[:100]}" for t in tasks
        ]
        prompt = f"""
Analyze these completed tasks for code quality patterns:

{chr(10).join(task_lines)}

Look for:
- Rushed work patterns
- Skipped testing
- Missing documentation
- Quality shortcuts

Report findings or None if all looks good.
"""
        return await self.think(prompt)

    async def _audit_documentation(self, tasks: list[dict[str, Any]]) -> str | None:
        """Audit documentation completeness."""
        missing_docs = [t for t in tasks if not t.get("documentation_complete")]
        if missing_docs:
            return f"Found {len(missing_docs)} tasks with incomplete documentation"
        return None

    async def _audit_process_compliance(
        self, tasks: list[dict[str, Any]]
    ) -> str | None:
        """Audit process compliance."""
        violations = [
            f"{t.get('title')} - no QA" for t in tasks if not t.get("qa_passed")
        ]
        if violations:
            return f"Process violations: {', '.join(violations)}"
        return None

    async def _perform_audit(self, audit_type: str) -> str | None:
        """Perform a specific type of audit."""
        audit_handlers = {
            "code_quality": self._audit_code_quality,
            "documentation": self._audit_documentation,
            "process_compliance": self._audit_process_compliance,
        }

        handler = audit_handlers.get(audit_type)
        if not handler:
            return None

        try:
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "completed", "limit": 10},
            )
            tasks = result.get("items", [])
            return await handler(tasks)
        except Exception as e:
            self.log.warning("Failed to perform audit", error=str(e))
            return None
