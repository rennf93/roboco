"""Structured content schema models.

Every human-facing artifact an agent produces — PR-review comments, task
notes, task descriptions, resumption context — is one of these typed models.
Each model:

- validates its named fields (non-trivial, required, controlled vocab);
- coerces a lone scalar where a list is declared (graceful, never a hard
  reject of the well-intentioned single-item case);
- renders to canonical labeled markdown via ``render_markdown()``.

The structured payload is the source of truth; the rendered markdown is the
derived mirror written to the Task's TEXT note columns and to PR comment
bodies. ``CONTENT_MODELS`` maps the gateway's content-type key to its model;
``validate_content`` is the single entry point that turns a raw payload into a
validated model (or a ``ContentValidationError``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from roboco.foundation.identity import CELL_TEAMS, Team

from .enums import Severity, Verdict
from .validators import ContentValidationError, coerce_to_list, reject_trivial

_SUMMARY_MIN = 10


class _Base(BaseModel):
    """Shared config: drop unknown keys (graceful), validate assignment."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class _Content(_Base):
    """A top-level content type that renders to canonical markdown."""

    def render_markdown(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i.strip()}" for i in items if i and i.strip())


def _section(title: str, body: str) -> str:
    body = body.strip()
    return f"## {title}\n{body}" if body else ""


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------------- #


class Finding(_Base):
    """One PR-review finding — file + line + expected vs actual."""

    file: str
    line: int | None = None
    severity: Severity
    criterion: str | None = None
    expected: str
    actual: str

    @field_validator("file", "expected", "actual")
    @classmethod
    def _nontrivial(cls, v: str, info: ValidationInfo) -> str:
        return reject_trivial(v, field=info.field_name or "field")


class WorkUnit(_Base):
    """One cell's slice of a task description."""

    team: Team
    summary: str
    items: list[str] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("team")
    @classmethod
    def _cell_team_only(cls, v: Team) -> Team:
        if v not in CELL_TEAMS:
            raise ValueError(
                f"team must be a cell ({', '.join(t.value for t in CELL_TEAMS)})"
            )
        return v

    @field_validator("summary")
    @classmethod
    def _nontrivial_summary(cls, v: str) -> str:
        return reject_trivial(v, field="summary")

    @field_validator("items")
    @classmethod
    def _nonempty_items(cls, v: list[str]) -> list[str]:
        cleaned = [i for i in v if i and i.strip()]
        if not cleaned:
            raise ValueError("items must list at least one work item")
        return cleaned


class AcVerdict(_Base):
    """One acceptance-criterion verdict from a QA review."""

    criterion: str
    status: Literal["verified", "failed", "na"]
    how: str

    @field_validator("criterion", "how")
    @classmethod
    def _nontrivial(cls, v: str, info: ValidationInfo) -> str:
        return reject_trivial(v, field=info.field_name or "field")


# --------------------------------------------------------------------------- #
# Top-level content models
# --------------------------------------------------------------------------- #


class PrReviewContent(_Content):
    """A PR-review comment / reviewer verdict."""

    summary: str
    findings: list[Finding] = Field(default_factory=list)
    verdict: Verdict

    @field_validator("findings", mode="before")
    @classmethod
    def _coerce_findings(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("summary")
    @classmethod
    def _nontrivial_summary(cls, v: str) -> str:
        return reject_trivial(v, field="summary", min_chars=_SUMMARY_MIN)

    def render_markdown(self) -> str:
        parts = [_section("Summary", self.summary)]
        if self.findings:
            rows = [
                "| File | Line | Severity | Expected → Actual |",
                "| --- | --- | --- | --- |",
            ]
            for f in self.findings:
                loc = str(f.line) if f.line is not None else "—"
                crit = f" ({f.criterion})" if f.criterion else ""
                rows.append(
                    f"| `{f.file}`{crit} | {loc} | {f.severity.value} "
                    f"| {f.expected} → {f.actual} |"
                )
            parts.append("## Findings\n" + "\n".join(rows))
        parts.append(_section("Verdict", self.verdict.value.replace("_", " ")))
        return _join(parts)


class TaskDescription(_Content):
    """A well-formed task description (shared by PM delegate + Intake draft)."""

    objective: str
    what_this_builds: list[str] = Field(default_factory=list)
    the_work: list[WorkUnit] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)

    @field_validator(
        "what_this_builds",
        "the_work",
        "notes",
        "constraints",
        "acceptance_criteria",
        mode="before",
    )
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("objective")
    @classmethod
    def _nontrivial_objective(cls, v: str) -> str:
        return reject_trivial(v, field="objective", min_chars=_SUMMARY_MIN)

    @field_validator("the_work")
    @classmethod
    def _nonempty_work(cls, v: list[WorkUnit]) -> list[WorkUnit]:
        if not v:
            raise ValueError("the_work must contain at least one work unit")
        return v

    @field_validator("acceptance_criteria")
    @classmethod
    def _nonempty_ac(cls, v: list[str]) -> list[str]:
        cleaned = [i for i in v if i and i.strip()]
        if not cleaned:
            raise ValueError("acceptance_criteria must list at least one criterion")
        return cleaned

    def render_markdown(self) -> str:
        parts = [_section("Objective", self.objective)]
        if self.what_this_builds:
            parts.append(_section("What This Builds", _bullets(self.what_this_builds)))
        if self.the_work:
            units = []
            for u in self.the_work:
                head = f"**{u.team.value.replace('_', ' ').title()}** — {u.summary}"
                units.append(f"{head}\n{_bullets(u.items)}")
            parts.append("## The Work\n" + "\n\n".join(units))
        if self.notes:
            parts.append(_section("Notes", _bullets(self.notes)))
        if self.constraints:
            parts.append(_section("Constraints", _bullets(self.constraints)))
        parts.append(
            _section("Acceptance Criteria", _bullets(self.acceptance_criteria))
        )
        return _join(parts)


class ResumptionNote(_Content):
    """The human handoff that lives in ``quick_context`` (no machine markers)."""

    done: str
    next: str
    where_to_look: list[str] = Field(default_factory=list)

    @field_validator("where_to_look", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("done", "next")
    @classmethod
    def _nontrivial(cls, v: str, info: ValidationInfo) -> str:
        return reject_trivial(v, field=info.field_name or "field")

    def render_markdown(self) -> str:
        parts = [_section("Done", self.done), _section("Next", self.next)]
        if self.where_to_look:
            parts.append(_section("Where to look", _bullets(self.where_to_look)))
        return _join(parts)


class DeveloperNote(_Content):
    """A developer's task note."""

    summary: str
    changes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)

    @field_validator("changes", "risks", "follow_ups", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("summary")
    @classmethod
    def _nontrivial(cls, v: str) -> str:
        return reject_trivial(v, field="summary", min_chars=_SUMMARY_MIN)

    def render_markdown(self) -> str:
        parts = [_section("Summary", self.summary)]
        if self.changes:
            parts.append(_section("Changes", _bullets(self.changes)))
        if self.risks:
            parts.append(_section("Risks", _bullets(self.risks)))
        if self.follow_ups:
            parts.append(_section("Follow-ups", _bullets(self.follow_ups)))
        return _join(parts)


class QaNote(_Content):
    """A QA review note — summary + per-criterion verdicts + outcome."""

    summary: str
    ac_verdicts: list[AcVerdict] = Field(default_factory=list)
    verdict: Verdict

    @field_validator("ac_verdicts", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("summary")
    @classmethod
    def _nontrivial(cls, v: str) -> str:
        return reject_trivial(v, field="summary", min_chars=_SUMMARY_MIN)

    @field_validator("verdict")
    @classmethod
    def _passed_or_failed(cls, v: Verdict) -> Verdict:
        if v not in (Verdict.PASSED, Verdict.FAILED):
            raise ValueError("QA verdict must be 'passed' or 'failed'")
        return v

    def render_markdown(self) -> str:
        parts = [_section("Summary", self.summary)]
        if self.ac_verdicts:
            marks = {"verified": "✅", "failed": "❌", "na": "—"}
            rows = [
                f"- {marks[a.status]} **{a.criterion}** — {a.how}"
                for a in self.ac_verdicts
            ]
            parts.append("## Acceptance Criteria\n" + "\n".join(rows))
        parts.append(_section("Verdict", self.verdict.value))
        return _join(parts)


class DocNote(_Content):
    """A documenter's note — what was documented vs deliberately skipped."""

    summary: str
    documented: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)

    @field_validator("documented", "skipped", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("summary")
    @classmethod
    def _nontrivial(cls, v: str) -> str:
        return reject_trivial(v, field="summary", min_chars=_SUMMARY_MIN)

    def render_markdown(self) -> str:
        parts = [_section("Summary", self.summary)]
        if self.documented:
            parts.append(_section("Documented", _bullets(self.documented)))
        if self.skipped:
            parts.append(_section("Skipped", _bullets(self.skipped)))
        return _join(parts)


class AuditorNote(_Content):
    """An auditor's confidential observation."""

    summary: str
    concerns: list[str] = Field(default_factory=list)
    severity: Literal["info", "watch", "risk"]

    @field_validator("concerns", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        return coerce_to_list(v)

    @field_validator("summary")
    @classmethod
    def _nontrivial(cls, v: str) -> str:
        return reject_trivial(v, field="summary", min_chars=_SUMMARY_MIN)

    def render_markdown(self) -> str:
        parts = [
            _section("Summary", self.summary),
            _section("Severity", self.severity),
        ]
        if self.concerns:
            parts.append(_section("Concerns", _bullets(self.concerns)))
        return _join(parts)


# --------------------------------------------------------------------------- #
# Registry + single validation entry point
# --------------------------------------------------------------------------- #

CONTENT_MODELS: dict[str, type[_Content]] = {
    "pr_review": PrReviewContent,
    "task_description": TaskDescription,
    "resumption": ResumptionNote,
    "developer": DeveloperNote,
    "qa": QaNote,
    "doc": DocNote,
    "auditor": AuditorNote,
}


def validate_content(content_type: str, payload: Any) -> _Content:
    """Validate ``payload`` against the model for ``content_type``.

    Returns the validated model (passed through unchanged if it is already an
    instance of the right model). Raises ``ContentValidationError(field,
    reason)`` — built from Pydantic's first error — on any failure, including an
    unknown content type.
    """
    model_cls = CONTENT_MODELS.get(content_type)
    if model_cls is None:
        raise ContentValidationError(
            "content_type", f"unknown content type: {content_type!r}"
        )
    if isinstance(payload, model_cls):
        return payload
    try:
        return model_cls.model_validate(
            payload if isinstance(payload, dict) else dict(payload or {})
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ())) or "?"
        raise ContentValidationError(loc, first.get("msg", "invalid")) from exc


def required_shape(content_type: str) -> dict[str, str]:
    """A ``{field: type-hint}`` map for a content type, for remediation hints."""
    model_cls = CONTENT_MODELS.get(content_type)
    if model_cls is None:
        return {}
    shape: dict[str, str] = {}
    for name, field in model_cls.model_fields.items():
        ann = field.annotation
        shape[name] = getattr(ann, "__name__", str(ann))
    return shape
