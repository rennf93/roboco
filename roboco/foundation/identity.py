"""Identity foundation — single source for roles, teams, agents, role-sets.

Replaces the parallel definitions across:
  - models/base.py (AgentRole, Team)
  - lifecycle/spec.py (Role)
  - agents_config.py (AGENT_ROLE_MAP, AGENT_TEAM_MAP, CELL_MEMBERS, PM_ROLES,
    _BOARD_ROLES)
  - seeds/initial_data.py (AGENT_UUIDS, DEFAULT_AGENTS slug+role+team fields)
  - services/permissions.py (PM_ROLES — 2-role variant)
  - runtime/orchestrator.py (_AGENT_TEAM_MAP + cell-prefix table)

Every consumer imports from here. Adding an agent edits exactly this file.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


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
    SYSTEM = "system"  # sentinel only — used for orchestrator-generated rows


class Team(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    UX_UI = "ux_ui"
    BOARD = "board"
    MAIN_PM = "main_pm"
    QA = "qa"
    MARKETING = "marketing"  # legacy — kept for seed-data parity, no agent declares it
    SYSTEM = "system"


class RoleLevel(IntEnum):
    SYSTEM = -1
    DEV = 1
    QA = 2
    DOCUMENTER = 3
    CELL_PM = 4
    MAIN_PM = 5
    BOARD = 6
    AUDITOR = 7  # observer override — sees everything
    CEO = 8
