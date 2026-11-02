"""Filed reports and playbook curation: the Head of Marketing's Periscope
market brief, the Auditor's Sentinel quality report, Coroner postmortem and
Librarian playbook drafts, and the playbook and vault curation verbs.

Mixins composed into ContentActions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.injection_guard import (
    screen_external_text_with_decisions,
)
from roboco.models.base import TaskStatus
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID


if TYPE_CHECKING:
    from roboco.services.gateway._content_actions_types import (
        ContentActionsSelf as _Base,
    )
else:
    _Base = object

logger = structlog.get_logger()


# Periscope market briefs are HoM-authored, mirroring _FEATURE_SPOTLIGHT_ROLES.
_PERISCOPE_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Market-brief free-text caps (spec §4 / Task 2). ``source_url`` is validated
# separately (a URL, not soup-checked prose) — see _reject_market_brief_url.
_MARKET_BRIEF_HEADLINE_MAX_CHARS = 200
_MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS = 500
_MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS = 300
_MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS = 300
_MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS = 500
# The threats / opportunities cap.
_MARKET_BRIEF_LIST_MAX_ITEMS = 5
_MARKET_BRIEF_LIST_ITEM_MAX_CHARS = 300

# Coroner postmortems are Auditor-authored — the one program the Auditor
# originates content for (spec §4), distinct from its curation-only
# approve/reject_playbook verbs.
_CORONER_ROLES: frozenset[str] = frozenset({"auditor"})
_CORONER_PROCESS_CHANGE_KINDS: frozenset[str] = frozenset(
    {"playbook", "prompt_fix", "conventions_rule", "other"}
)
_CORONER_INCIDENT_SUMMARY_MAX_CHARS = 500
_CORONER_ROOT_CAUSE_MAX_CHARS = 800
_CORONER_PROCESS_CHANGE_DESC_MAX_CHARS = 800

# Sentinel (Board Program) quality reports are Auditor-authored — a bounded
# expansion mirroring _PEST_ROLES/_PERISCOPE_ROLES.
_SENTINEL_ROLES: frozenset[str] = frozenset({"auditor"})

# Quality-report free-text caps (spec §4).
_QUALITY_REPORT_HEADLINE_MAX_CHARS = 200
_QUALITY_REPORT_ITEM_OBSERVATION_MAX_CHARS = 500
_QUALITY_REPORT_ITEM_EVIDENCE_MAX_CHARS = 500
_QUALITY_REPORT_ITEM_SUGGESTED_ACTION_MAX_CHARS = 300
_QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS = 800
_QUALITY_REPORT_AREAS: frozenset[str] = frozenset(
    {"waivers", "findings", "conventions", "budget", "docs", "other"}
)

# Librarian (Board Program) playbook drafts are Auditor-authored, mirroring
# _SENTINEL_ROLES. Each draft is created directly via PlaybookService (the
# Coroner _draft_coroner_playbook precedent) — the Auditor does NOT also gain
# draft_playbook (see _DRAFT_PLAYBOOK_ROLES below / role_config.py).
_LIBRARIAN_ROLES: frozenset[str] = frozenset({"auditor"})
# Matches PlaybookCreate.title's own cap.
_PLAYBOOK_DRAFT_TITLE_MAX_CHARS = 200
_PLAYBOOK_DRAFT_BODY_MAX_CHARS = 4000
_PLAYBOOK_DRAFT_PATTERN_EVIDENCE_MAX_CHARS = 500

# Playbook curation RBAC: delivery roles DRAFT; only the Auditor CURATES.
# The Auditor is deliberately NOT in this set — "auditor curates but does not
# draft" is an enforced invariant (test_playbook_verbs.py). A Coroner
# postmortem's playbook-kind process change is drafted through a DIFFERENT
# path — directly via PlaybookService inside propose_postmortem, never this
# do-verb (see _draft_coroner_playbook below) — so the invariant holds even
# though the Auditor now originates playbook drafts by another route; that
# draft rides the SAME pending-playbook curation queue every delivery-role
# draft does, curated by the Auditor same as any other, never self-approved
# inline.
_DRAFT_PLAYBOOK_ROLES: frozenset[str] = frozenset(
    {"developer", "qa", "documenter", "cell_pm", "main_pm"}
)
_CURATE_PLAYBOOK_ROLES: frozenset[str] = frozenset({"auditor"})

# Vault-curation: only the Auditor writes a root task-tree's narrative
# (mirrors the playbook-curation bounded-expansion pattern above).
_CURATE_VAULT_ROLES: frozenset[str] = frozenset({"auditor"})


def _normalize_market_brief_finding(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw market-brief finding into the stored marker
    shape. Mirrors ``_normalize_pest_hunt_item`` — ``id`` is server-assigned.

    ``status``/``reject_reason``/``materialized_task_id`` mirror the roadmap/
    pest-hunt item shape even though the exploration task itself completes
    at propose time — the finding still carries its OWN per-item CEO
    decision (``PeriscopeService.approve_finding``/``reject_finding``),
    orthogonal to the task's own terminal status.
    """
    return {
        "id": f"finding-{idx}",
        "claim": str(raw["claim"]).strip(),
        "source_url": str(raw["source_url"]).strip(),
        "relevance": str(raw["relevance"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_quality_report_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw quality-report item into the stored marker
    shape. Mirrors ``_normalize_market_brief_finding`` — ``id`` is
    server-assigned, and the same per-item ``status`` triple applies (see
    ``SentinelService.approve_item``/``reject_item``)."""
    return {
        "id": f"item-{idx}",
        "area": str(raw["area"]).strip(),
        "observation": str(raw["observation"]).strip(),
        "evidence": str(raw["evidence"]).strip(),
        "suggested_action": str(raw["suggested_action"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _render_market_brief_for_screening(
    headline: str,
    findings: list[dict[str, Any]],
    threats: list[str],
    opportunities: list[str],
    positioning_note: str,
) -> str:
    """One line per content piece — every line is independently checked by
    ``screen_external_text``, so a single injected line among otherwise-clean
    web-derived content is flagged without dropping the rest of the brief."""
    lines = [f"Headline: {headline}"]
    for f in findings:
        lines.append(f"Finding: {f['claim']} (source: {f['source_url']})")
        lines.append(f"Relevance: {f['relevance']}")
    lines.extend(f"Threat: {t}" for t in threats)
    lines.extend(f"Opportunity: {o}" for o in opportunities)
    if positioning_note:
        lines.append(f"Positioning: {positioning_note}")
    return "\n".join(lines)


class PeriscopeActions(_Base):
    """Periscope: the Head of Marketing's weekly market-research brief, each
    finding's validation (claim, cited source, relevance), the threats and
    opportunities lists, the injection screen on persist, and the CEO's
    nudge."""

    @staticmethod
    def _reject_market_brief_url(url: str, idx: int) -> Envelope | None:
        """An uncited market claim is noise (spec Task 2) — validate
        ``source_url`` parses as a real http(s) URL rather than soup-checking
        it as prose."""
        if len(url) > _MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} source_url is {len(url)} chars, over the "
                    f"{_MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s source_url",
                context_briefing={},
            )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Envelope.invalid_state(
                message=f"finding {idx} source_url {url!r} is not a valid http(s) URL",
                remediate=(
                    f"provide a real http(s) URL finding {idx}'s claim came from"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_finding(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw market-brief finding dict; None when clean."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"finding {idx} is not an object",
                remediate="each finding needs claim/source_url/relevance",
                context_briefing={},
            )
        if rej := cls._reject_market_brief_finding_claim(raw, idx):
            return rej
        if rej := cls._reject_market_brief_finding_source(raw, idx):
            return rej
        return cls._reject_market_brief_finding_relevance(raw, idx)

    @classmethod
    def _reject_market_brief_finding_claim(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        claim = raw.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            return Envelope.invalid_state(
                message=f"finding {idx} is missing 'claim'",
                remediate=f"provide a substantive claim for finding {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(claim, field=f"finding {idx} claim", min_chars=8):
            return rej
        if len(claim) > _MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} claim is {len(claim)} chars, over the "
                    f"{_MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s claim",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_finding_source(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        source_url = raw.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} is missing 'source_url' — an uncited "
                    "market claim is noise"
                ),
                remediate=(
                    f"provide the http(s) source URL finding {idx}'s claim came from"
                ),
                context_briefing={},
            )
        return cls._reject_market_brief_url(source_url, idx)

    @classmethod
    def _reject_market_brief_finding_relevance(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        relevance = raw.get("relevance")
        if not isinstance(relevance, str) or not relevance.strip():
            return Envelope.invalid_state(
                message=f"finding {idx} is missing 'relevance'",
                remediate=f"provide a substantive relevance for finding {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            relevance, field=f"finding {idx} relevance", min_chars=8
        ):
            return rej
        if len(relevance) > _MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} relevance is {len(relevance)} chars, over "
                    f"the {_MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s relevance",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_text_list(
        cls, values: Any, *, field: str
    ) -> Envelope | None:
        """Validate an optional ``threats``/``opportunities`` list: at most
        ``_MARKET_BRIEF_LIST_MAX_ITEMS`` substantive strings, each capped at
        ``_MARKET_BRIEF_LIST_ITEM_MAX_CHARS``. ``None`` (omitted) is clean."""
        if values is None:
            return None
        if not isinstance(values, list) or len(values) > _MARKET_BRIEF_LIST_MAX_ITEMS:
            return Envelope.invalid_state(
                message=(
                    f"{field} must be a list of at most "
                    f"{_MARKET_BRIEF_LIST_MAX_ITEMS} strings"
                ),
                remediate=f"provide at most {_MARKET_BRIEF_LIST_MAX_ITEMS} {field}",
                context_briefing={},
            )
        for i, v in enumerate(values):
            if rej := cls._reject_market_brief_list_item(v, field=field, idx=i):
                return rej
        return None

    @classmethod
    def _reject_market_brief_list_item(
        cls, value: Any, *, field: str, idx: int
    ) -> Envelope | None:
        """One ``threats``/``opportunities`` entry — split out of
        ``_reject_market_brief_text_list`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        if not isinstance(value, str) or not value.strip():
            return Envelope.invalid_state(
                message=f"{field}[{idx}] is empty",
                remediate=f"provide substantive text for {field}[{idx}] or drop it",
                context_briefing={},
            )
        if rej := cls._reject_soup(value, field=f"{field}[{idx}]", min_chars=4):
            return rej
        if len(value) > _MARKET_BRIEF_LIST_ITEM_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"{field}[{idx}] is {len(value)} chars, over the "
                    f"{_MARKET_BRIEF_LIST_ITEM_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten {field}[{idx}]",
                context_briefing={},
            )
        return None

    async def propose_market_brief(
        self,
        *,
        agent_id: UUID,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None = None,
        opportunities: list[str] | None = None,
        positioning_note: str = "",
    ) -> Envelope:
        """Head of Marketing files ONE Periscope weekly market-research brief
        — competitors, adjacent-tool releases, positioning shifts — delivered
        as a held REPORT to the CEO.

        Unlike ``propose_roadmap``/``propose_bug_hunt`` (a per-item CEO queue
        that keeps the exploration task open until every item is decided),
        this mirrors ``propose_feature_spotlight``'s complete-at-propose
        asymmetry: a report has no per-item decision, so the exploration task
        completes in this same call. The brief is screened through
        ``injection_guard.screen_external_text`` before persisting — it is
        web-derived content that later reaches the roadmap exploration
        prompt, same untrusted-text posture as X mentions / vault notes
        (screen-and-flag, never drop).
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_market_brief",
            payload={
                "headline": headline,
                "findings": findings,
                "threats": threats,
                "opportunities": opportunities,
                "positioning_note": positioning_note,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _PERISCOPE_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a market brief; only the "
                    "Head of Marketing authors one"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if rej := self._reject_market_brief_fields(
            headline, findings, threats, opportunities, positioning_note
        ):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_periscope_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_market_brief(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open periscope exploration task assigned to you",
                remediate=(
                    "propose_market_brief only runs against an active "
                    "exploration cycle spawned by the periscope engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )

        await self._persist_market_brief(
            task,
            headline=headline,
            findings=findings,
            threats=threats,
            opportunities=opportunities,
            positioning_note=positioning_note,
        )
        await self._notify_periscope_brief(task, headline.strip())
        return Envelope.ok(
            status="market_brief_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reads the brief as a report in the panel",
            context_briefing={
                "headline": headline.strip(),
                "finding_count": len(findings),
            },
        )

    async def _persist_market_brief(
        self,
        task: Any,
        *,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None,
        opportunities: list[str] | None,
        positioning_note: str,
    ) -> None:
        """Normalize, screen, persist, and complete — split out of
        ``propose_market_brief`` to keep its own cyclomatic complexity under
        the xenon budget. Complete-at-propose: a report has no per-item CEO
        decision to wait on (the x_feature asymmetry, not the roadmap/
        pest-control per-item flow) — BoardProgramEngine's dedup ledger
        auto-closes the cycle row the moment it next checks this now-terminal
        exploration task.
        """
        normalized_findings = [
            _normalize_market_brief_finding(idx, raw)
            for idx, raw in enumerate(findings)
        ]
        normalized_threats = [str(t).strip() for t in (threats or [])]
        normalized_opportunities = [str(o).strip() for o in (opportunities or [])]
        normalized_note = positioning_note.strip()
        screened = await screen_external_text_with_decisions(
            self.task.session,
            _render_market_brief_for_screening(
                headline,
                normalized_findings,
                normalized_threats,
                normalized_opportunities,
                normalized_note,
            ),
            source=f"periscope_brief:{task.id}",
        )
        if screened.flagged:
            logger.warning(
                "periscope: injection pattern detected in market brief",
                task_id=str(task.id),
                hits=screened.hits,
            )
        markers.set_market_brief(
            task,
            {
                "headline": headline.strip(),
                "findings": normalized_findings,
                "threats": normalized_threats,
                "opportunities": normalized_opportunities,
                "positioning_note": normalized_note,
                "injection_hits": screened.hits,
            },
        )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

    @classmethod
    def _reject_market_brief_findings_list(
        cls, findings: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-finding validation loop, split out of
        ``_reject_market_brief_fields`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_findings = PROGRAMS["periscope"].max_items_per_cycle
        if not (1 <= len(findings) <= max_findings):
            return Envelope.invalid_state(
                message=(
                    f"a brief needs 1-{max_findings} cited findings, got "
                    f"{len(findings)}"
                ),
                remediate=f"propose between 1 and {max_findings} cited findings",
                context_briefing={},
            )
        for idx, raw in enumerate(findings):
            if rej := cls._reject_market_brief_finding(raw, idx):
                return rej
        return None

    @classmethod
    def _reject_market_brief_fields(
        cls,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None,
        opportunities: list[str] | None,
        positioning_note: str,
    ) -> Envelope | None:
        """Full field validation for ``propose_market_brief``, split out to
        keep the verb's own return-statement count under the xenon/PLR0911
        budget."""
        if rej := cls._reject_soup(headline, field="headline", min_chars=8):
            return rej
        if len(headline) > _MARKET_BRIEF_HEADLINE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"headline is {len(headline)} chars, over the "
                    f"{_MARKET_BRIEF_HEADLINE_MAX_CHARS}-char cap"
                ),
                remediate="shorten the headline",
                context_briefing={},
            )
        if rej := cls._reject_market_brief_findings_list(findings):
            return rej
        if rej := cls._reject_market_brief_text_list(threats, field="threats"):
            return rej
        if rej := cls._reject_market_brief_text_list(
            opportunities, field="opportunities"
        ):
            return rej
        return cls._reject_market_brief_positioning_note(positioning_note)

    @classmethod
    def _reject_market_brief_positioning_note(cls, value: str) -> Envelope | None:
        """Split out of ``_reject_market_brief_fields`` to keep its own
        return-statement count under the xenon/PLR0911 budget. Optional —
        empty is clean; only a non-empty value is soup/length-checked."""
        if not value or not value.strip():
            return None
        if rej := cls._reject_soup(value, field="positioning_note", min_chars=8):
            return rej
        if len(value) > _MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"positioning_note is {len(value)} chars, over the "
                    f"{_MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS}-char cap"
                ),
                remediate="shorten positioning_note",
                context_briefing={},
            )
        return None

    async def _notify_periscope_brief(self, task: Any, headline: str) -> None:
        """Best-effort CEO nudge the moment a market brief lands — ONE call
        per cycle (a report, not N queue items), so unlike
        ``_notify_pest_hunt_items``/``_notify_roadmap_items`` this fires once,
        not per-finding."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_periscope_brief(
                    task=task, task_id=task.id, headline=headline
                )
        except Exception as exc:
            logger.warning(
                "periscope telegram notify failed (best-effort)", error=str(exc)
            )


class SentinelActions(_Base):
    """Sentinel: the Auditor's weekly state-of-quality report, each item's
    validation, persisting it, and the CEO's nudge."""

    @staticmethod
    def _reject_quality_report_item_area(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        area = raw.get("area")
        if not isinstance(area, str) or area.strip() not in _QUALITY_REPORT_AREAS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} area {area!r} must be one of "
                    f"{sorted(_QUALITY_REPORT_AREAS)}"
                ),
                remediate=(
                    f"set item {idx}'s area to one of {sorted(_QUALITY_REPORT_AREAS)}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_quality_report_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate observation/evidence/suggested_action of one quality-
        report item dict — split from ``_reject_quality_report_item`` to
        keep its own xenon complexity budget."""
        for field, max_chars in (
            ("observation", _QUALITY_REPORT_ITEM_OBSERVATION_MAX_CHARS),
            ("evidence", _QUALITY_REPORT_ITEM_EVIDENCE_MAX_CHARS),
            ("suggested_action", _QUALITY_REPORT_ITEM_SUGGESTED_ACTION_MAX_CHARS),
        ):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(value, field=f"item {idx} {field}", min_chars=8):
                return rej
            if len(value) > max_chars:
                return Envelope.invalid_state(
                    message=(
                        f"item {idx} {field} is {len(value)} chars, over the "
                        f"{max_chars}-char cap"
                    ),
                    remediate=f"shorten item {idx}'s {field}",
                    context_briefing={},
                )
        return None

    @classmethod
    def _reject_quality_report_item(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw quality-report item dict; None when clean.
        Mirrors ``_reject_market_brief_finding``."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item needs area/observation/evidence/suggested_action"
                ),
                context_briefing={},
            )
        if rej := cls._reject_quality_report_item_area(raw, idx):
            return rej
        return cls._reject_quality_report_item_text_fields(raw, idx)

    @classmethod
    def _reject_quality_report_items(
        cls, items: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-item validation loop, split out of
        ``_reject_quality_report_fields`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_items = PROGRAMS["sentinel"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a quality report needs 1-{max_items} items, got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        for idx, raw in enumerate(items):
            if rej := cls._reject_quality_report_item(raw, idx):
                return rej
        return None

    @classmethod
    def _reject_quality_report_fields(
        cls, headline: str, items: list[dict[str, Any]], overall_assessment: str
    ) -> Envelope | None:
        """Full field validation for ``propose_quality_report``, split out to
        keep the verb's own return-statement count under the xenon/PLR0911
        budget."""
        if rej := cls._reject_soup(headline, field="headline", min_chars=8):
            return rej
        if len(headline) > _QUALITY_REPORT_HEADLINE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"headline is {len(headline)} chars, over the "
                    f"{_QUALITY_REPORT_HEADLINE_MAX_CHARS}-char cap"
                ),
                remediate="shorten the headline",
                context_briefing={},
            )
        if rej := cls._reject_quality_report_items(items):
            return rej
        if rej := cls._reject_soup(
            overall_assessment, field="overall_assessment", min_chars=8
        ):
            return rej
        if len(overall_assessment) > _QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"overall_assessment is {len(overall_assessment)} chars, "
                    "over the "
                    f"{_QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS}-char cap"
                ),
                remediate="shorten overall_assessment",
                context_briefing={},
            )
        return None

    async def propose_quality_report(
        self,
        *,
        agent_id: UUID,
        headline: str,
        items: list[dict[str, Any]],
        overall_assessment: str,
    ) -> Envelope:
        """Auditor files ONE Sentinel weekly "state of quality" report —
        waiver-accumulation trends, conventions-violation hotspots, budget
        anomalies — delivered as a held REPORT to the CEO.

        Mirrors ``propose_market_brief``'s complete-at-propose asymmetry: a
        report has no per-item CEO decision, so the exploration task
        completes in this same call. Unlike ``propose_market_brief`` this is
        deliberately NOT screened through
        ``injection_guard.screen_external_text`` — every input here is
        internal org data (the findings ledger, the conventions table, the
        spend tables, the Auditor's own read of the codebase), never
        untrusted web/external text, so there is nothing to screen.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_quality_report",
            payload={
                "headline": headline,
                "items": items,
                "overall_assessment": overall_assessment,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _SENTINEL_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a quality report; only "
                    "the Auditor authors one"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_quality_report_fields(
            headline, items, overall_assessment
        ):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_sentinel_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_quality_report(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open sentinel exploration task assigned to you",
                remediate=(
                    "propose_quality_report only runs against an active "
                    "exploration cycle spawned by the sentinel engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )

        await self._persist_quality_report(
            task,
            headline=headline,
            items=items,
            overall_assessment=overall_assessment,
        )
        await self._notify_quality_report(task, headline.strip())
        return Envelope.ok(
            status="quality_report_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reads the report in the panel",
            context_briefing={
                "headline": headline.strip(),
                "item_count": len(items),
            },
        )

    async def _persist_quality_report(
        self,
        task: Any,
        *,
        headline: str,
        items: list[dict[str, Any]],
        overall_assessment: str,
    ) -> None:
        """Normalize, persist, and complete — split out of
        ``propose_quality_report`` to keep its own cyclomatic complexity
        under the xenon budget. Complete-at-propose: mirrors
        ``_persist_market_brief``."""
        normalized_items = [
            _normalize_quality_report_item(idx, raw) for idx, raw in enumerate(items)
        ]
        markers.set_quality_report(
            task,
            {
                "headline": headline.strip(),
                "items": normalized_items,
                "overall_assessment": overall_assessment.strip(),
            },
        )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

    async def _notify_quality_report(self, task: Any, headline: str) -> None:
        """Best-effort CEO nudge the moment a quality report lands — ONE call
        per cycle (a report, not N queue items), mirrors
        ``_notify_periscope_brief``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_sentinel_report(
                    task=task, task_id=task.id, headline=headline
                )
        except Exception as exc:
            logger.warning(
                "sentinel telegram notify failed (best-effort)", error=str(exc)
            )


class CoronerActions(_Base):
    """Coroner: the Auditor's postmortem on its open autopsy task, its
    validation, the process-change playbook it drafts, and the
    notification."""

    @classmethod
    def _reject_postmortem_text_fields(
        cls, incident_summary: str, root_cause: str
    ) -> Envelope | None:
        """Soup + length caps on the postmortem's two free-text narrative
        fields, folded into one caller-side check (xenon/PLR0911 budget —
        mirrors ``_reject_feature_spotlight_fields``)."""
        if rej := cls._reject_soup(
            incident_summary, field="incident_summary", min_chars=20
        ):
            return rej
        if len(incident_summary) > _CORONER_INCIDENT_SUMMARY_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"incident_summary is {len(incident_summary)} chars, over "
                    f"the {_CORONER_INCIDENT_SUMMARY_MAX_CHARS}-char limit"
                ),
                remediate="shorten incident_summary",
                context_briefing={},
            )
        if rej := cls._reject_soup(root_cause, field="root_cause", min_chars=20):
            return rej
        if len(root_cause) > _CORONER_ROOT_CAUSE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"root_cause is {len(root_cause)} chars, over the "
                    f"{_CORONER_ROOT_CAUSE_MAX_CHARS}-char limit"
                ),
                remediate="shorten root_cause",
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_postmortem_failed_stage(failed_stage: str) -> Envelope | None:
        """``failed_stage`` must be a real lifecycle status — never a made-up
        label — so it stays comparable across postmortems."""
        from roboco.models.base import TaskStatus

        valid = {s.value for s in TaskStatus}
        if failed_stage not in valid:
            return Envelope.invalid_state(
                message=f"failed_stage {failed_stage!r} is not a real task status",
                remediate=f"pass one of: {sorted(valid)}",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_postmortem_process_change(cls, process_change: Any) -> Envelope | None:
        if not isinstance(process_change, dict):
            return Envelope.invalid_state(
                message="process_change must be an object",
                remediate="pass process_change={'kind': ..., 'description': ...}",
                context_briefing={},
            )
        kind = process_change.get("kind")
        if kind not in _CORONER_PROCESS_CHANGE_KINDS:
            return Envelope.invalid_state(
                message=f"process_change.kind {kind!r} is invalid",
                remediate=(
                    f"kind must be one of {sorted(_CORONER_PROCESS_CHANGE_KINDS)}"
                ),
                context_briefing={},
            )
        description = str(process_change.get("description", ""))
        if rej := cls._reject_soup(
            description, field="process_change.description", min_chars=15
        ):
            return rej
        if len(description) > _CORONER_PROCESS_CHANGE_DESC_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"process_change.description is {len(description)} chars, "
                    f"over the {_CORONER_PROCESS_CHANGE_DESC_MAX_CHARS}-char limit"
                ),
                remediate="shorten process_change.description",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_postmortem_playbook(
        cls, process_change: dict[str, Any], playbook: dict[str, Any] | None
    ) -> Envelope | None:
        """``playbook`` is required iff ``process_change.kind == 'playbook'``
        (spec §4) — never optional-but-ignored on that kind, never demanded
        on any other."""
        if process_change.get("kind") != "playbook":
            return None
        if not isinstance(playbook, dict):
            return Envelope.invalid_state(
                message="process_change.kind='playbook' requires a playbook",
                remediate="pass playbook={'title': ..., 'body': ...}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            str(playbook.get("title", "")), field="playbook.title", min_chars=5
        ):
            return rej
        return cls._reject_soup(
            str(playbook.get("body", "")), field="playbook.body", min_chars=20
        )

    @classmethod
    def _reject_postmortem(
        cls,
        incident_summary: str,
        root_cause: str,
        failed_stage: str,
        process_change: Any,
        playbook: dict[str, Any] | None,
    ) -> Envelope | None:
        if rej := cls._reject_postmortem_text_fields(incident_summary, root_cause):
            return rej
        if rej := cls._reject_postmortem_failed_stage(failed_stage):
            return rej
        if rej := cls._reject_postmortem_process_change(process_change):
            return rej
        return cls._reject_postmortem_playbook(process_change, playbook)

    async def _draft_coroner_playbook(
        self, agent_id: UUID, incident_summary: str, playbook: dict[str, Any]
    ) -> tuple[str | None, Envelope | None]:
        """Create the process-change playbook DRAFT directly via
        ``PlaybookService`` — not the ``draft_playbook`` do-verb, since this
        call already runs inside the Auditor-only ``propose_postmortem`` gate.
        The Auditor also carries ``draft_playbook`` on its manifest
        (role_config.py) so a coroner-authored draft is indistinguishable
        from any other in the pending-playbook curation queue — reviewed and
        approved/rejected there same as any delivery-role draft, never
        self-approved in this same call. Returns (playbook_id, None) on
        success or (None, rejection_envelope) on a title conflict — checked
        BEFORE any postmortem-task mutation so a conflict is a clean,
        retryable rejection, not a half-completed autopsy."""
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        try:
            drafted = await get_playbook_service(self.task.session).draft(
                PlaybookCreate(
                    title=str(playbook["title"]).strip(),
                    problem=incident_summary.strip(),
                    procedure=str(playbook["body"]).strip(),
                    tags=["coroner", "postmortem"],
                ),
                created_by=agent_id,
                source_program="coroner",
            )
        except ConflictError as exc:
            return None, Envelope.invalid_state(
                message=str(exc),
                remediate="use a more distinct playbook title (slug must be unique)",
                context_briefing={},
            )
        return str(drafted.id), None

    async def propose_postmortem(
        self,
        *,
        agent_id: UUID,
        incident_summary: str,
        root_cause: str,
        failed_stage: str,
        process_change: dict[str, Any],
        playbook: dict[str, Any] | None = None,
    ) -> Envelope:
        """Auditor authors ONE Coroner postmortem on its open autopsy task,
        completing it in the same call — no per-item CEO queue (spec §4:
        a report, not a list of items the CEO decides one by one). Call
        exactly once per autopsy cycle.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_postmortem",
            payload={
                "incident_summary": incident_summary,
                "root_cause": root_cause,
                "failed_stage": failed_stage,
                "process_change": process_change,
                "playbook": playbook,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _CORONER_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a postmortem; only the "
                    "Auditor authors one"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_postmortem(
            incident_summary, root_cause, failed_stage, process_change, playbook
        ):
            return rej

        from roboco.services.coroner_engine import get_coroner_engine
        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_coroner_cycles()
        task = next((t for t in cycles if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open Coroner autopsy task assigned to you",
                remediate=(
                    "propose_postmortem only runs against an active autopsy "
                    "spawned by the coroner engine; wait for the next incident"
                ),
                context_briefing={},
            )

        playbook_id: str | None = None
        if process_change["kind"] == "playbook":
            playbook_id, rej = await self._draft_coroner_playbook(
                agent_id, incident_summary, playbook or {}
            )
            if rej is not None:
                return rej

        engine = get_coroner_engine(self.task.session)
        await engine.complete_with_postmortem(
            task,
            {
                "incident_summary": incident_summary.strip(),
                "root_cause": root_cause.strip(),
                "failed_stage": failed_stage,
                "process_change": {
                    "kind": process_change["kind"],
                    "description": str(process_change["description"]).strip(),
                    # A "playbook" kind already routed straight into the
                    # playbook curation queue above — nothing left for the
                    # CEO to decide on THIS process change, so it never
                    # enters the proposed/approved/rejected per-item flow
                    # (CoronerService.approve_process_change/
                    # reject_process_change refuse it outright).
                    "status": (
                        "not_applicable"
                        if process_change["kind"] == "playbook"
                        else "proposed"
                    ),
                    "reject_reason": None,
                    "materialized_task_id": None,
                },
                "playbook_id": playbook_id,
            },
        )
        await self._notify_postmortem(task, incident_summary, process_change["kind"])
        return Envelope.ok(
            status="postmortem_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO is notified; no per-item decision needed",
            context_briefing={
                "failed_stage": failed_stage,
                "process_change_kind": process_change["kind"],
                "playbook_id": playbook_id,
            },
        )

    async def _notify_postmortem(
        self, task: Any, incident_summary: str, process_change_kind: str
    ) -> None:
        """Best-effort push notification the moment a postmortem lands —
        mirrors ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_postmortem(
                    task=task,
                    task_id=task.id,
                    incident_summary=incident_summary,
                    process_change_kind=process_change_kind,
                )
        except Exception as exc:
            logger.warning(
                "coroner postmortem telegram notify failed (best-effort)",
                error=str(exc),
            )


class LibrarianActions(_Base):
    """Librarian: the Auditor's playbook drafts mined from repeated patterns,
    each draft's validation, dedup within the batch and against live
    playbooks, and the CEO's nudge."""

    @classmethod
    def _reject_playbook_draft_item(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw playbook-draft dict; None when clean. Mirrors
        ``_reject_quality_report_item_text_fields``'s loop-over-fields shape."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"draft {idx} is not an object",
                remediate="each draft needs title/body/pattern_evidence",
                context_briefing={},
            )
        for field, min_chars, max_chars in (
            ("title", 5, _PLAYBOOK_DRAFT_TITLE_MAX_CHARS),
            ("body", 20, _PLAYBOOK_DRAFT_BODY_MAX_CHARS),
            ("pattern_evidence", 15, _PLAYBOOK_DRAFT_PATTERN_EVIDENCE_MAX_CHARS),
        ):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"draft {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for draft {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"draft {idx} {field}", min_chars=min_chars
            ):
                return rej
            if len(value) > max_chars:
                return Envelope.invalid_state(
                    message=(
                        f"draft {idx} {field} is {len(value)} chars, over the "
                        f"{max_chars}-char cap"
                    ),
                    remediate=f"shorten draft {idx}'s {field}",
                    context_briefing={},
                )
        return None

    @staticmethod
    def _reject_playbook_draft_duplicate_titles(
        drafts: list[dict[str, Any]],
    ) -> Envelope | None:
        """Case-insensitive dedup WITHIN this batch — split out so the
        title-conflict message is distinct from the per-item field check."""
        seen: set[str] = set()
        for idx, raw in enumerate(drafts):
            title = str(raw.get("title", "")).strip().lower()
            if title in seen:
                return Envelope.invalid_state(
                    message=f"draft {idx} title duplicates another draft in this batch",
                    remediate="give each draft a distinct title",
                    context_briefing={},
                )
            seen.add(title)
        return None

    @classmethod
    def _reject_playbook_drafts_batch(
        cls, drafts: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-draft validation + in-batch dedup, split out
        of ``propose_playbook_drafts`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_drafts = PROGRAMS["librarian"].max_items_per_cycle
        if not (1 <= len(drafts) <= max_drafts):
            return Envelope.invalid_state(
                message=f"propose 1-{max_drafts} playbook drafts, got {len(drafts)}",
                remediate=f"propose between 1 and {max_drafts} drafts",
                context_briefing={},
            )
        for idx, raw in enumerate(drafts):
            if rej := cls._reject_playbook_draft_item(raw, idx):
                return rej
        return cls._reject_playbook_draft_duplicate_titles(drafts)

    async def _reject_playbook_drafts_existing_titles(
        self, drafts: list[dict[str, Any]]
    ) -> Envelope | None:
        """Live, unbounded case-insensitive dedup against every non-archived
        playbook already in the store — the mining prompt only shows the
        most-recent 20 as a hint, so this re-checks fresh at propose time
        rather than trusting what the Auditor read minutes ago."""
        from roboco.services.librarian_engine import get_librarian_engine

        existing = await get_librarian_engine(
            self.task.session
        ).existing_playbook_titles_lower()
        for idx, raw in enumerate(drafts):
            title = str(raw["title"]).strip()
            if title.lower() in existing:
                return Envelope.invalid_state(
                    message=(
                        f"draft {idx} title {title!r} duplicates an existing playbook"
                    ),
                    remediate=(
                        "give this draft a distinct title, or drop it — a playbook "
                        "for this pattern may already exist"
                    ),
                    context_briefing={},
                )
        return None

    async def _draft_librarian_playbooks(
        self, agent_id: UUID, drafts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, str]], Envelope | None]:
        """Create each validated draft as a real DRAFT playbook via
        ``PlaybookService.draft()`` directly — the Coroner precedent
        (``_draft_coroner_playbook`` above), never the ``draft_playbook``
        do-verb, so "auditor curates but does not draft" stays true at the
        do-verb surface even though the Auditor originates these drafts.

        A per-item ``ConflictError`` (a genuine same-tick race — the live
        pre-check in ``_reject_playbook_drafts_existing_titles`` already
        closed the realistic window) aborts the rest of the batch with a
        clean rejection.
        ponytail: no rollback of earlier successes in this loop — any draft
        already created before the conflict stays a real, independently
        valid playbook riding the normal curation queue (just not
        cross-referenced on this particular report); this is the same
        residual race Coroner already accepts, and Librarian's own
        single-open-cycle dedup makes a same-cycle collision vanishingly
        rare in practice.
        """
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        svc = get_playbook_service(self.task.session)
        created: list[dict[str, str]] = []
        for idx, raw in enumerate(drafts):
            try:
                drafted = await svc.draft(
                    PlaybookCreate(
                        title=str(raw["title"]).strip(),
                        problem=str(raw["pattern_evidence"]).strip(),
                        procedure=str(raw["body"]).strip(),
                        tags=["librarian", "auto-authored"],
                    ),
                    created_by=agent_id,
                    source_program="librarian",
                )
            except ConflictError as exc:
                return created, Envelope.invalid_state(
                    message=f"draft {idx}: {exc}",
                    remediate="use a more distinct title (slug must be unique)",
                    context_briefing={"drafted_before_conflict": len(created)},
                )
            created.append({"id": str(drafted.id), "title": drafted.title})
        return created, None

    async def propose_playbook_drafts(
        self, *, agent_id: UUID, drafts: list[dict[str, Any]]
    ) -> Envelope:
        """Auditor mines journals/learnings for repeated patterns and drafts
        1-3 playbooks on its open Librarian cycle task, completing it in the
        same call (mirrors ``propose_market_brief``/``propose_quality_report``'s
        complete-at-propose asymmetry — a mining cycle has no per-item CEO
        decision to wait on).

        Each draft is created via ``PlaybookService.draft()`` DIRECTLY — the
        same Coroner precedent ``_draft_coroner_playbook`` established: the
        Auditor does NOT also carry ``draft_playbook`` on its manifest
        (``role_config.py``, ``test_playbook_verbs.py``'s "auditor curates
        but does not draft" invariant), so a Librarian-authored draft reaches
        the pending-playbook curation queue through this direct service
        call, never the do-verb every delivery role uses. A LATER Auditor
        spawn curates them — a deliberate, documented self-curation
        asymmetry (see ``agents/prompts/identities/auditor.md``).
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_playbook_drafts",
            payload={"drafts": drafts},
        )
        role = await self._caller_role(agent_id)
        if role not in _LIBRARIAN_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose playbook drafts; only the "
                    "Auditor mines for patterns"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_playbook_drafts_batch(drafts):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_librarian_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_playbook_drafts(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open Librarian mining task assigned to you",
                remediate=(
                    "propose_playbook_drafts only runs against an active mining "
                    "cycle spawned by the librarian engine; wait for the next cycle"
                ),
                context_briefing={},
            )

        if rej := await self._reject_playbook_drafts_existing_titles(drafts):
            return rej

        created, rej = await self._draft_librarian_playbooks(agent_id, drafts)
        if rej is not None:
            return rej

        markers.set_playbook_drafts(task, {"drafts": created})
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()
        await self._notify_librarian_drafts(task, created)
        return Envelope.ok(
            status="playbook_drafts_proposed",
            task_id=str(task.id),
            next=(
                "i_am_idle() — the drafts ride the normal pending-playbook "
                "curation queue"
            ),
            context_briefing={"draft_count": len(created)},
        )

    async def _notify_librarian_drafts(
        self, task: Any, created: list[dict[str, str]]
    ) -> None:
        """Best-effort CEO nudge the moment Librarian mines its drafts —
        mirrors ``_notify_postmortem``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_librarian_drafts(
                    task=task,
                    task_id=task.id,
                    titles=[d["title"] for d in created],
                )
        except Exception as exc:
            logger.warning(
                "librarian telegram notify failed (best-effort)", error=str(exc)
            )


class PlaybookActions(_Base):
    """Playbooks and the vault: delivery roles draft a playbook, the Auditor
    approves, rejects or archives it, and the Auditor writes a root task
    tree's vault narrative."""

    async def draft_playbook(
        self,
        *,
        agent_id: UUID,
        title: str,
        problem: str,
        procedure: str,
        tags: list[str] | None = None,
        source_task_id: UUID | None = None,
    ) -> Envelope:
        """Draft a curated playbook (delivery roles); the Auditor approves it."""
        role = await self._caller_role(agent_id)
        if role not in _DRAFT_PLAYBOOK_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not draft a playbook",
                remediate="Only delivery roles draft playbooks; the Auditor curates.",
                context_briefing={},
            )
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        try:
            playbook = await get_playbook_service(self.task.session).draft(
                PlaybookCreate(
                    title=title,
                    problem=problem,
                    procedure=procedure,
                    tags=tags or [],
                    source_task_id=source_task_id,
                ),
                created_by=agent_id,
            )
        except ConflictError as exc:
            return Envelope.invalid_state(
                message=str(exc),
                remediate="Use a more distinct title (the slug must be unique).",
                context_briefing={},
            )
        return Envelope.ok(
            status="playbook_drafted",
            task_id=None,
            next="continue",
            context_briefing={
                "playbook_id": str(playbook.id),
                "playbook_status": "draft",
            },
        )

    async def approve_playbook(self, *, agent_id: UUID, playbook_id: UUID) -> Envelope:
        """Auditor approves a draft playbook (-> approved + indexed)."""
        return await self._curate_playbook(
            agent_id=agent_id, playbook_id=playbook_id, action="approve"
        )

    async def reject_playbook(
        self, *, agent_id: UUID, playbook_id: UUID, reason: str
    ) -> Envelope:
        """Auditor rejects a playbook (-> archived, with a reason)."""
        return await self._curate_playbook(
            agent_id=agent_id,
            playbook_id=playbook_id,
            action="reject",
            reason=reason,
        )

    async def archive_playbook(self, *, agent_id: UUID, playbook_id: UUID) -> Envelope:
        """Auditor archives an approved playbook (-> archived, retired)."""
        return await self._curate_playbook(
            agent_id=agent_id,
            playbook_id=playbook_id,
            action="archive",
        )

    async def _curate_playbook(
        self,
        *,
        agent_id: UUID,
        playbook_id: UUID,
        action: str,
        reason: str = "",
    ) -> Envelope:
        """Shared Auditor-only curation path for approve / reject / archive."""
        role = await self._caller_role(agent_id)
        if role not in _CURATE_PLAYBOOK_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not curate playbooks",
                remediate="Only the Auditor approves/rejects/archives playbooks.",
                context_briefing={},
            )
        from roboco.services.base import ConflictError, NotFoundError
        from roboco.services.playbook import get_playbook_service

        svc = get_playbook_service(self.task.session)
        try:
            if action == "approve":
                playbook = await svc.approve(playbook_id, approver_id=agent_id)
                status = "playbook_approved"
            elif action == "archive":
                playbook = await svc.archive(playbook_id, approver_id=agent_id)
                status = "playbook_archived"
            else:
                playbook = await svc.reject(
                    playbook_id, approver_id=agent_id, reason=reason or action
                )
                status = "playbook_archived"
        except NotFoundError:
            return Envelope.not_found(message=f"playbook {playbook_id} not found")
        except ConflictError as exc:
            # A status-precondition violation (approve/reject on a non-draft,
            # archive on a non-approved) is a clean invalid_state, not a 500 —
            # the agent gets a remediate hint to re-fetch the playbook's
            # current status before re-trying.
            return Envelope.invalid_state(
                message=str(exc),
                remediate=(
                    "Only a draft can be approved/rejected; only an approved "
                    "playbook can be archived. Re-list drafts/approved to see "
                    "the playbook's current status before re-trying."
                ),
                context_briefing={"playbook_id": str(playbook_id)},
            )
        # Commit the status change BEFORE touching the RAG index: the index write
        # runs through its own auto-committing connection, so indexing before the
        # status commit would durably land (or drop) a playbook in the corpus even
        # if this transaction rolled back — a divergence agents surface in
        # briefings. ``get_db`` commits the session again after the route returns
        # (a no-op on the now-clean transaction); this explicit commit is what
        # gates the index. A poisoned session (a prior mid-verb failure rolled it
        # back -> PendingRollbackError) must NOT 500 the curation verb NOR fall
        # through to index an uncommitted playbook: surface a clean invalid_state
        # and skip the index. See #55.
        from sqlalchemy.exc import PendingRollbackError

        try:
            await self.task.session.commit()
        except PendingRollbackError:
            logger.warning(
                "playbook curate: gating commit on a rolled-back session",
                action=action,
                playbook_id=str(playbook_id),
            )
            return Envelope.invalid_state(
                message=(
                    "the DB session was rolled back by a prior failure; the "
                    "playbook status change was not committed"
                ),
                remediate=(
                    "re-fetch the playbook's current status and re-try the "
                    "curation verb"
                ),
                context_briefing={"playbook_id": str(playbook_id)},
            )
        if action == "approve":
            await svc.index_approved(playbook)
        else:
            await svc.unindex_playbook(playbook)
        return Envelope.ok(
            status=status,
            task_id=None,
            next="continue",
            context_briefing={
                "playbook_id": str(playbook.id),
                "playbook_status": str(playbook.status),
            },
        )

    async def curate_vault(
        self, *, agent_id: UUID, task_id: UUID | str, narrative: str
    ) -> Envelope:
        """Auditor-only: write a root task-tree's vault narrative section.

        Fully re-materializes the task's note (parent/subtasks/dependencies
        resolved fresh) with ``narrative`` filling the ``## Narrative``
        section a deterministic write otherwise leaves as a placeholder.
        """
        resolved = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved, Envelope):
            return resolved
        task_id = resolved
        role = await self._caller_role(agent_id)
        if role not in _CURATE_VAULT_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not curate the vault",
                remediate="Only the Auditor writes vault narratives.",
                context_briefing={},
            )
        if not settings.obsidian_vault_enabled:
            return Envelope.invalid_state(
                message="the Obsidian vault is disabled",
                remediate="ROBOCO_OBSIDIAN_VAULT_ENABLED is off — nothing to curate.",
                context_briefing={},
            )
        task = await self.task.get(task_id)
        if task is None:
            return Envelope.not_found(message=f"task {task_id} not found")

        from roboco.services.project import get_project_service
        from roboco.services.vault_assembly import assemble_task_note_data
        from roboco.services.vault_writer import get_vault_writer

        try:
            data = await assemble_task_note_data(
                self.task,
                get_project_service(self.task.session),
                task,
                narrative=narrative,
            )
            get_vault_writer().write_task(data)
        except Exception as exc:
            logger.warning(
                "vault curation write failed", task_id=str(task_id), error=str(exc)
            )
            return Envelope.invalid_state(
                message=f"vault write failed: {exc}",
                remediate="retry curate_vault; check ROBOCO_VAULT_PATH is writable",
                context_briefing={},
            )
        return Envelope.ok(
            status="vault_curated",
            task_id=str(task_id),
            next="continue",
            context_briefing={},
        )
