"""Agent Gateway — server-side orchestration layer.

Composes existing services and enforcement to expose intent-verb behavior
to the new MCP servers (roboco-flow, roboco-do). Logic lives here; MCP
servers are protocol shims.

See docs/superpowers/specs/2026-05-01-agent-gateway-design.md for the
full design rationale.
"""

from __future__ import annotations

__all__ = [
    "envelope",
    "remediation",
    "role_config",
    "claimant_lock",
    "trigger_filter",
    "tracing_gate",
    "evidence_builder",
    "merge_chain",
    "commit_validator",
    "choreographer",
]
