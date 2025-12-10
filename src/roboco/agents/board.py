"""
Board Agents (Product Owner, Head of Marketing, Auditor)

Implementation of Board-level workflows from the blueprint.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, Team

logger = structlog.get_logger()


# =============================================================================
# PRODUCT OWNER
# =============================================================================


class ProductOwnerPhase(str, Enum):
    """Phases of the Product Owner lifecycle."""

    VISION = "vision"
    ROADMAP = "roadmap"
    DEFINE = "define"
    PRIORITIZE = "prioritize"
    REVIEW = "review"
    FEEDBACK = "feedback"


@dataclass
class Feature:
    """A feature or epic."""

    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    priority: int  # 0-3
    status: str = "backlog"


class ProductOwnerAgent(Agent):
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

    async def find_work(self) -> UUID | None:
        """Product Owner always has work."""
        return self.id

    async def execute_task(self, task_id: UUID) -> bool:
        """Execute Product Owner duties."""
        try:
            match self._current_phase:
                case ProductOwnerPhase.VISION:
                    await self._phase_vision()
                    self._current_phase = ProductOwnerPhase.ROADMAP

                case ProductOwnerPhase.ROADMAP:
                    await self._phase_roadmap()
                    self._current_phase = ProductOwnerPhase.DEFINE

                case ProductOwnerPhase.DEFINE:
                    await self._phase_define()
                    self._current_phase = ProductOwnerPhase.PRIORITIZE

                case ProductOwnerPhase.PRIORITIZE:
                    await self._phase_prioritize()
                    self._current_phase = ProductOwnerPhase.REVIEW

                case ProductOwnerPhase.REVIEW:
                    await self._phase_review()
                    self._current_phase = ProductOwnerPhase.FEEDBACK

                case ProductOwnerPhase.FEEDBACK:
                    await self._phase_feedback()
                    self._current_phase = ProductOwnerPhase.VISION

            return False

        except Exception as e:
            self.log.error(
                "Error in PO phase", phase=self._current_phase.value, error=str(e)
            )
            return False

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
        # TODO: Check against acceptance criteria
        return True


# =============================================================================
# HEAD OF MARKETING
# =============================================================================


class HeadMarketingPhase(str, Enum):
    """Phases of the Head of Marketing lifecycle."""

    RESEARCH = "research"
    STRATEGY = "strategy"
    PLAN = "plan"
    CREATE = "create"
    EXECUTE = "execute"
    ANALYZE = "analyze"


@dataclass
class Campaign:
    """A marketing campaign."""

    id: UUID
    name: str
    objective: str
    channels: list[str]
    start_date: datetime | None = None
    end_date: datetime | None = None
    status: str = "planning"
    metrics: dict[str, Any] = field(default_factory=dict)


class HeadMarketingAgent(Agent):
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

    async def find_work(self) -> UUID | None:
        """Head of Marketing always has work."""
        return self.id

    async def execute_task(self, task_id: UUID) -> bool:
        """Execute marketing duties."""
        try:
            match self._current_phase:
                case HeadMarketingPhase.RESEARCH:
                    await self._phase_research()
                    self._current_phase = HeadMarketingPhase.STRATEGY

                case HeadMarketingPhase.STRATEGY:
                    await self._phase_strategy()
                    self._current_phase = HeadMarketingPhase.PLAN

                case HeadMarketingPhase.PLAN:
                    await self._phase_plan()
                    self._current_phase = HeadMarketingPhase.CREATE

                case HeadMarketingPhase.CREATE:
                    await self._phase_create()
                    self._current_phase = HeadMarketingPhase.EXECUTE

                case HeadMarketingPhase.EXECUTE:
                    await self._phase_execute()
                    self._current_phase = HeadMarketingPhase.ANALYZE

                case HeadMarketingPhase.ANALYZE:
                    await self._phase_analyze()
                    self._current_phase = HeadMarketingPhase.RESEARCH

            return False

        except Exception as e:
            self.log.error(
                "Error in marketing phase",
                phase=self._current_phase.value,
                error=str(e),
            )
            return False

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


class AuditorPhase(str, Enum):
    """Phases of the Auditor lifecycle."""

    OBSERVE = "observe"
    ANALYZE = "analyze"
    FLAG = "flag"
    REPORT = "report"
    AUDIT = "audit"
    ADVISE = "advise"


class FlagSeverity(str, Enum):
    """Severity of flagged issues."""

    INFO = "info"
    WARNING = "warning"
    CONCERN = "concern"
    CRITICAL = "critical"


@dataclass
class AuditFlag:
    """A flagged issue from audit observation."""

    id: UUID
    severity: FlagSeverity
    category: str  # quality, process, communication, efficiency
    description: str
    evidence: list[str]
    recommendation: str | None = None
    reported_to_ceo: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AuditReport:
    """A report to the CEO."""

    period: str
    summary: str
    flags: list[AuditFlag]
    metrics: dict[str, Any]
    recommendations: list[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class AuditorAgent(Agent):
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

    async def find_work(self) -> UUID | None:
        """Auditor always has work - watching everything."""
        return self.id

    async def execute_task(self, task_id: UUID) -> bool:
        """Execute Auditor duties."""
        try:
            match self._current_phase:
                case AuditorPhase.OBSERVE:
                    await self._phase_observe()
                    self._current_phase = AuditorPhase.ANALYZE

                case AuditorPhase.ANALYZE:
                    await self._phase_analyze()
                    self._current_phase = AuditorPhase.FLAG

                case AuditorPhase.FLAG:
                    await self._phase_flag()
                    self._current_phase = AuditorPhase.REPORT

                case AuditorPhase.REPORT:
                    await self._phase_report()
                    self._current_phase = AuditorPhase.AUDIT

                case AuditorPhase.AUDIT:
                    await self._phase_audit()
                    self._current_phase = AuditorPhase.ADVISE

                case AuditorPhase.ADVISE:
                    await self._phase_advise()
                    self._current_phase = AuditorPhase.OBSERVE

            return False

        except Exception as e:
            self.log.error(
                "Error in auditor phase", phase=self._current_phase.value, error=str(e)
            )
            return False

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
                        "timestamp": datetime.utcnow(),
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

        prompt = f"""
Analyze these observations for quality and efficiency issues:

Observations:
{chr(10).join(str(o) for o in self._observations[-50:])}

Look for:
1. Efficiency issues - wasted effort, unclear processes
2. Communication breakdowns - unanswered questions, confusion
3. Quality concerns - shortcuts, skipped steps
4. Process violations - skipping QA, missing documentation
5. Team health - frustration, conflicts

For each issue found, provide:
- Category
- Severity (info/warning/concern/critical)
- Description
- Evidence
- Recommendation

Be thorough but fair.
"""
        analysis = await self.think(prompt)
        self.log.info("Analysis complete", analysis_length=len(analysis))

        # Parse and create flags (simplified)
        if "concern" in analysis.lower() or "critical" in analysis.lower():
            from uuid import uuid4

            self._flags.append(
                AuditFlag(
                    id=uuid4(),
                    severity=FlagSeverity.CONCERN,
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

        critical_flags = [f for f in self._flags if f.severity == FlagSeverity.CRITICAL]
        if critical_flags:
            # Immediate alert to CEO
            await self._alert_ceo(critical_flags)

    async def _phase_report(self) -> None:
        """
        REPORT phase: Private report to CEO.
        """
        self.log.debug("REPORT phase")

        # Check if it's time for regular report
        should_report = (
            self._last_report is None
            or (datetime.utcnow() - self._last_report).hours >= 24
            or any(
                f.severity in [FlagSeverity.CONCERN, FlagSeverity.CRITICAL]
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
            self._last_report = datetime.utcnow()
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
                from uuid import uuid4

                self._flags.append(
                    AuditFlag(
                        id=uuid4(),
                        severity=FlagSeverity.INFO,
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
        # TODO: Query messaging API with silent flag
        return []

    async def _alert_ceo(self, flags: list[AuditFlag]) -> None:
        """Send immediate alert to CEO."""
        self.log.warning("CEO alert sent", flags=len(flags))

    async def _send_ceo_report(self, report: AuditReport) -> None:
        """Send private report to CEO."""
        self.log.info("CEO report sent", period=report.period)

    async def _perform_audit(self, audit_type: str) -> str | None:
        """Perform a specific type of audit."""
        # TODO: Implement actual audits
        return None


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================


def create_product_owner(
    name: str = "Product Owner",
    system_prompt: str | None = None,
) -> ProductOwnerAgent:
    """Factory function to create the Product Owner agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/board/product-owner.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Product Owner."

    config = AgentConfig(
        name=name,
        slug="product-owner",
        role=AgentRole.PRODUCT_OWNER,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["requirements", "prioritization", "acceptance"],
        can_notify=True,
    )

    return ProductOwnerAgent(config)


def create_head_marketing(
    name: str = "Head of Marketing",
    system_prompt: str | None = None,
) -> HeadMarketingAgent:
    """Factory function to create the Head of Marketing agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/board/head-marketing.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Head of Marketing."

    config = AgentConfig(
        name=name,
        slug="head-marketing",
        role=AgentRole.HEAD_MARKETING,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["marketing", "campaigns", "analytics"],
        can_notify=True,
    )

    return HeadMarketingAgent(config)


def create_auditor(
    name: str = "Auditor",
    system_prompt: str | None = None,
) -> AuditorAgent:
    """Factory function to create the Auditor agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/board/auditor.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Auditor - the CEO's silent ally."

    config = AgentConfig(
        name=name,
        slug="auditor",
        role=AgentRole.AUDITOR,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["observation", "analysis", "audit", "ceo_reporting"],
        can_notify=True,
    )

    return AuditorAgent(config)
