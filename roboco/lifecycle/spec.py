"""Canonical lifecycle + permissions spec.

Single source of truth for:
  - task lifecycle status transitions
  - per-role permissions on atomic actions
  - per-role permissions on gateway intent verbs
  - claim restrictions
  - team-based access rules
  - self-review prevention rules

Every consumer (choreographer, MCP manifest, RAG corpus, agent prompts,
panel UI, tests, middleware) reads its behavior from this module.

Predecessor canon (prose):
  - docs/internal/old/workflows/STATUS_TRANSITIONS.md
  - docs/internal/old/workflows/PERMISSIONS.md

If this module disagrees with those documents, the discrepancy is
recorded in the spec design doc:
  docs/superpowers/specs/2026-05-09-lifecycle-canonical-spec-design.md
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"
    CELL_PM = "cell_pm"
    MAIN_PM = "main_pm"
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"
    CEO = "ceo"


class Status(StrEnum):
    BACKLOG = "backlog"
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    VERIFYING = "verifying"
    AWAITING_QA = "awaiting_qa"
    NEEDS_REVISION = "needs_revision"
    AWAITING_DOCUMENTATION = "awaiting_documentation"
    AWAITING_PM_REVIEW = "awaiting_pm_review"
    AWAITING_CEO_APPROVAL = "awaiting_ceo_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskType(StrEnum):
    CODE = "code"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    PLANNING = "planning"
    DESIGN = "design"
    ADMINISTRATIVE = "administrative"
