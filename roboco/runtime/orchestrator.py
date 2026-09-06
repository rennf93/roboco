"""
Agent Orchestrator

Manages Claude Code containers for all RoboCo agents.
Handles spawning, monitoring, health checks, and graceful shutdown.

The orchestrator is the BRAIN of the system:
- Checks for work BEFORE spawning agents (no wasteful spawns)
- Claims tasks on behalf of agents before spawning
- Agents receive their assignment at spawn time
- Agents scan for more work after completing a task
- Agents only call i_am_idle() when truly no work remains
"""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from roboco.llm.providers import ProviderRegistry
    from roboco.services.uptime import UptimeLedger
import httpx
import structlog
from fastapi import status as http_status

from roboco.agents.factories._base import compose_prompt
from roboco.agents_config import (
    ALL_DOCS,
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.identity import (
    CELL_TEAMS,
    is_human_only_role,
    is_spawnable_agent_slug,
    is_worktree_author_role,
    role_for_slug_or_none,
)
from roboco.foundation.policy.agent_loop import DEFAULT_BUDGET as _AGENT_LOOP_BUDGET
from roboco.foundation.policy.batch import is_branchless_coordination
from roboco.foundation.policy.content import markers as _markers
from roboco.models import AgentRole, Team
from roboco.models.base import ModelProvider
from roboco.models.runtime import (
    MODEL_MAP,
    ROLE_EFFORT_MAP,
    ROLE_MODEL_MAP,
    AgentInstance,
    OrchestratorAgentConfig,
    OrchestratorAgentState,
    SpawnGitContext,
    WaitingRecord,
)
from roboco.models.sandbox import SandboxInfo
from roboco.runtime.compose_labels import compose_label_args
from roboco.runtime.sandbox import SandboxProvisioner
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.task import (
    BARFLY_SOURCE,
    CORONER_SOURCE,
    DOGFOOD_SOURCE,
    LIBRARIAN_SOURCE,
    MEGAPHONE_SOURCE,
    MIRROR_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    PR_REVIEW_SOURCES,
    RELEASE_MANAGER_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    SELF_HEAL_SOURCE,
    SENTINEL_SOURCE,
    SPACKLE_SOURCE,
    VIDEO_HELD_SOURCES,
    VIDEO_SOURCE,
    WAR_ROOM_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    X_SOURCES,
)

logger = structlog.get_logger()

# Every name below this point that is not otherwise referenced in this file is
# still imported here on purpose: this module used to define (or import) it
# directly, and orchestrator.py's own namespace is a documented patch/import
# target (roboco.runtime.orchestrator.<name>) for callers and tests even
# after its only USE moved into roboco/runtime/engines/* during the 2026-09-06
# god-class decomposition. Listed in __all__ so ruff's unused-import check
# (F401) treats them as intentional re-exports rather than dead imports.
__all__ = [
    "ALL_DOCS",
    "CELL_TEAMS",
    "ROLE_EFFORT_MAP",
    "ROLE_MODEL_MAP",
    "UTC",
    "VIDEO_SOURCE",
    "AgentRole",
    "Team",
    "cast",
    "compose_label_args",
    "compose_prompt",
    "get_escalation_target",
    "hashlib",
    "http_status",
    "httpx",
    "is_human_only_role",
    "is_spawnable_agent_slug",
    "is_worktree_author_role",
    "json",
    "re",
    "role_for_slug_or_none",
    "shutil",
    "tempfile",
    "time",
    "timedelta",
]

# Reverse mapping: UUID -> slug
UUID_TO_SLUG = {uuid: slug for slug, uuid in AGENT_UUIDS.items()}

# Re-export for backwards compatibility
AgentState = OrchestratorAgentState
AgentConfig = OrchestratorAgentConfig

# Docker configuration
AGENT_NETWORK = "roboco_default"
AGENT_BASE_IMAGE = "roboco-agent-base"

# Minutes in an hour, for formatting an elapsed duration as "Xh Ym".
_MINUTES_PER_HOUR = 60

# Supersede contributor PR comments - a coherent pair. The at-supersede comment
# posts when the CEO takes the PR over; the at-close comment posts when the
# replacement lands and the contributor PR is retired. Both are prefixed with an
# ``@{author}`` tag (when the contributor's login is known) so the original
# developer is notified and nudged toward the replacement PR.
SUPERSEDE_PR_COMMENT = (
    "Thanks for this contribution! We reviewed it and are taking the work "
    "over internally to finish and harden it to our standards. The "
    "implementation continues on branch `{branch}` as an internal task; "
    "your PR informed the approach and is appreciated. This PR will be "
    "closed once our replacement lands. Feel free to reach out with any "
    "questions."
)
SUPERSEDE_PR_CLOSE_COMMENT = (
    "Our replacement PR #{replacement_pr} has been merged, finishing and "
    "hardening the work to our standards based on your contribution. This "
    "PR is closed as superseded. Thanks for the contribution! If we missed "
    "something from your PR, please open a new one and we will pick it up."
)


def _supersede_author_prefix(author: str) -> str:
    """The ``@login `` prefix for a supersede comment, or "" when unknown."""
    return f"@{author} " if author else ""


# Max background branch-cut attempts before escalating to BLOCKED (HUMAN
# resolver). The sweep retries with exponential backoff between attempts.
_MAX_BRANCH_CUT_ATTEMPTS = 3
_BRANCH_CUT_BACKOFF_BASE_SECONDS = 60.0

# Port on which each agent's Claude Code SDK server listens inside its container.
# Referenced by write-hooks (_finalize_spawn_session, _sweep_token_snapshots,
# _sweep_budget_exceeded) to build the SDK health/usage URL.
SDK_PORT: int = 9000

# Provider-recovery probe: a free, unmetered liveness call confirms a parked
# provider is accepting requests again before parked agents are resumed.
# Listing models / tags costs no tokens; only a 2xx response means recovered
# (a 429 rate limit OR a 5xx overload both keep the provider parked).
_ANTHROPIC_PROBE_BASE = "https://api.anthropic.com"
_PROBE_TIMEOUT_SECONDS = 10.0
# Docker subprocess deadlines for the reaper path. A hung Docker daemon or a
# stuck container FS would otherwise freeze the single asyncio event loop: the
# reaper runs inline before every dispatch tick and shares that loop with every
# background sweeper. Generous enough that a legitimate slow docker call (a
# loaded daemon, a cold-venv ``import httpx, mcp``) is never wrongly aborted;
# short enough that a hang degrades one tick, not the whole fleet.
_DOCKER_INSPECT_TIMEOUT_SECONDS = 10.0
_DOCKER_EXEC_TIMEOUT_SECONDS = 30.0
# Deadline for draining fire-and-forget ``_bg_tasks`` on shutdown. Short DB
# writes (a respawn_tracker upsert, an audit-log row) finish before the
# process exits — preserving the durable PM-respawn counter and the
# metrics-bearing audit trail — while a stuck task can't hang shutdown: past
# this deadline the still-pending tasks are cancelled. Generous enough that a
# legitimate slow write under load commits rather than being dropped (the
# exact data-loss tail the durable tracker exists to prevent).
_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 5.0
# Attribution breadcrumbs for orchestrator-initiated container stops (see
# _record_expected_stop). A breadcrumb older than this is treated as unrelated
# to whatever exit the monitor is now looking at, rather than mis-attributed.
_EXPECTED_STOP_FRESH_SECONDS = 120.0
_EXPECTED_STOP_MAX_ENTRIES = 200
# _route_unassigned_pm_task's creator-skip guard: a PM that just created a task
# is about to assign it one tool-call later, so racing in and claiming it for
# the PM would hijack the delegation. That's only true for a few seconds —
# past this grace the creator's session is long gone and the skip would hold
# the task pending-unassigned forever. Generous enough to cover the PM's next
# tool call; short enough that a genuinely abandoned task recovers fast.
_CREATOR_ROUTE_GRACE_SECONDS = 600
# None of the status-bucket dispatchers pass `team=` to GET /tasks; team,
# source, and routing filters all run per-task after the fetch. GET /tasks's
# default limit (100), ordered oldest-eligible-first (see
# TaskService.fifo_order), silently truncates once a status bucket exceeds
# it org-wide: a materialized but not-yet-eligible task can fill the window
# and bury an eligible younger one, i.e. "assigned but nothing routes it".
# Raised to the route's own declared max (Query(..., le=500) on GET /tasks)
# so a fetching dispatcher sees the whole queue any realistically-sized
# backlog can produce. Shared by every dispatcher whose fetch is not
# team-scoped: _dispatch_pm_work, _dispatch_dev_work, _dispatch_qa_work,
# _dispatch_pr_review_work, _dispatch_pr_gate_work, _dispatch_doc_work.
_PM_DISPATCH_FETCH_LIMIT = 500
# Validation reason suffix for a child whose parent is still cutting its
# branch inside the provisioning grace: a healthy, recurring wait, not a fault.
_PARENT_BRANCH_WAIT = "waiting for parent branch provisioning"
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_OK = 200
_HTTP_MULTIPLE_CHOICES = 300  # first non-2xx status; 2xx == [_HTTP_OK, this)

# The orchestrator calls its own write API as a trusted internal actor. Those
# routes require an agent identity (X-Agent-ID); a self-call without it is
# rejected 401, so silent recovery ops (auto-block / auto-resume / auto-recover
# / SLA annotation) no-op and paused/blocked parents wedge. The system identity
# holds TaskAction.ASSIGN, so it is authorized for the audited admin_set_status
# path those routes use. EVERY dispatcher client that can reach the API must
# carry it — header propagation was previously inconsistent across the separate
# AsyncClient call-sites, so only some paths were authenticated.
_SYSTEM_API_HEADERS = {
    "X-Agent-ID": "00000000-0000-0000-0000-000000000000",
    "X-Agent-Role": "system",
}


def _system_api_headers() -> dict[str, str]:
    """System identity headers for the orchestrator's internal self-API calls.

    Wraps ``_SYSTEM_API_HEADERS`` and adds a signed ``X-Agent-Token`` for the
    system identity (F038/F039). Without it, arming
    ``ROBOCO_AGENT_AUTH_REQUIRED=true`` 401s every silent recovery op
    (auto-block / auto-resume / auto-recover / SLA annotation) and wedges
    paused/blocked parents — the prior self-PATCH 401 fix only carried
    ``X-Agent-ID`` / ``X-Agent-Role``, so it was incomplete under auth-required.
    When the HMAC secret is unset (dev), ``issue_agent_token`` returns the
    ``UNSIGNED`` sentinel and auth isn't required, so the self-call still
    succeeds; the header is present either way so a future arm-when-secret-set
    doesn't silently break.
    """
    from roboco.agents_config import issue_agent_token

    return {
        **_SYSTEM_API_HEADERS,
        "X-Agent-Token": issue_agent_token(
            _SYSTEM_API_HEADERS["X-Agent-ID"], "system", ""
        ),
    }


def _agent_api_headers(agent_uuid: str, role: str) -> dict[str, str]:
    """Headers for the orchestrator's internal self-API calls acting as a
    specific agent (the cell-PM auto-submit). Adds the signed ``X-Agent-Token``
    + ``X-Agent-Team`` so the call passes the ``ROBOCO_AGENT_AUTH_REQUIRED``
    gate — a hand-built ``{X-Agent-ID, X-Agent-Role}`` dict 401s with
    "Missing X-Agent-Token" under auth-required (F038/F039 — the same gap the
    system-headers helper closes for the system identity).

    The token is attached only when ``ROBOCO_AGENT_AUTH_SECRET`` is set: the
    dev-mode middleware rejects a presented-but-unverifiable token (the
    ``UNSIGNED`` sentinel) with 401 "signature mismatch" while accepting a
    missing token, so sending ``UNSIGNED`` would turn a clean dev self-call
    into a 401. With the secret armed the token is signed and verifies.
    """
    from roboco.agents_config import _auth_secret, issue_agent_token

    team = get_agent_team(agent_uuid) or ""
    headers = {"X-Agent-ID": agent_uuid, "X-Agent-Role": role}
    if team:
        headers["X-Agent-Team"] = team
    if _auth_secret():
        headers["X-Agent-Token"] = issue_agent_token(agent_uuid, role, team)
    return headers


# Consecutive failed recovery probes before the CEO is notified once per episode.
_CEO_NOTIFY_THRESHOLD = 10
# Consecutive strategy-engine cycle failures before the CEO is notified once
# per failure episode (#193). Mirrors _CEO_NOTIFY_THRESHOLD so a persistently
# failing assess() (bad DB / goals row) surfaces instead of silently producing
# nothing every tick.
_STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD = 10
# Persistent-probe-failure escape hatch (F094): if the recovery probe keeps
# failing past this threshold, the probe endpoint itself is the problem (a
# misconfigured URL, a removed API key, a network partition to the probe host)
# while the provider may well be fine for real workloads. Hold the park any
# longer and every agent on the provider strands forever with only a one-shot
# CEO notification. Past this threshold, fall back to the same time-expiry
# optimism the unprobeable-provider path uses (``_do_probe`` returns True when
# there is no probe URL): clear the park and resume. If the provider is
# genuinely still down the real workload attempts re-park via the 429/5xx path,
# so this is bounded burn — strictly better than a silent forever-strand. Kept
# above the CEO-notify threshold so the operator gets the notification first.
_PROBE_GIVE_UP_THRESHOLD = 30

# Persistent server-overload parking (HTTP 529 / 500 / 503). The model API's
# SDK already retries transient overloads in-process; only a persistent one
# survives to kill the run. When it does, park the provider like a 429 instead
# of crash-retrying into the overload. These markers are matched (lowercased,
# substring) against the tail of the dead container's own output, so they are
# kept specific to how the API surfaces an overload. Bare "error 529"/"error
# 500"/"error 503" were dropped (F037): an agent that merely writes about an
# HTTP status code in its own notes ("the endpoint returned error 500,
# retrying") would false-match and park the whole Anthropic fleet. The SDK
# error formatter emits "API Error: NNN" + a JSON error type, so the
# ``api error: NNN`` and type-string markers below cover every real overload
# without that false-match surface.
_OVERLOAD_RETRY_AFTER_S = 45.0
_ANTHROPIC_OVERLOAD_MARKERS: tuple[str, ...] = (
    "overloaded_error",
    "internal_server_error",
    "api error: 529",
    "api error: 500",
    "api error: 503",
)

# Session / usage-limit parking (HTTP 429). The Claude session ("5-hour") limit
# crashes the agent container with a 0-token rejection that is NOT a 5xx
# overload, so without its own markers it falls through to crash-respawn —
# straight back into the limit until the window resets. Park the provider like a
# 429 instead and let the probe-resume loop revive the parked tasks once the
# quota clears. Markers are specific to how the session limit surfaces (matched
# lowercased, substring) so they can't false-match an agent writing about
# limits; the probe (which also hits the same limit) keeps the park until reset.
# Reuses the longer overload retry cadence — probing a multi-hour window every
# few seconds is wasteful, and each probe is itself a rejected call.
_RATE_LIMIT_RETRY_AFTER_S = 300.0
_ANTHROPIC_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "hit your session limit",
    "five_hour",
)

# Host Claude Code OAuth credential expiry (~/.claude bind-mounted into every
# agent container). The Claude Code structured auth-failure event key
# fragment, unreachable from agent prose; a Read of this source file inside a
# transcript is JSON-escaped (error\":\"authentication_failed) so it cannot
# false-match.
_ANTHROPIC_AUTH_MARKERS: tuple[str, ...] = ('error":"authentication_failed',)
# A dead credential is fixed by a human running `claude login` on the host;
# probing every 10 minutes bounds the re-park burn to one container start per
# parked agent per window.
_ANTHROPIC_AUTH_RETRY_AFTER_S = 600.0
# ollama.com HTTP 429 body (the weekly glm-5.3:cloud limit surfaces here).
# Specific to the API error formatter so an agent writing about limits can't
# false-match and park the whole ollama fleet.
_OLLAMA_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate limit exceeded",
    # The Claude SDK's own structured api_retry system line (e.g.
    # "error_status":429,"error":"rate_limit"), unreachable from agent prose
    # because a plain sentence cannot contain these JSON-key fragments.
    'error_status":429',
    'error":"rate_limit',
)

# LOCAL (self-hosted Ollama) rides the Ollama API shape: the same 429 bodies
# and the same SDK retry lines as the cloud provider.
_LOCAL_RATE_LIMIT_MARKERS = _OLLAMA_RATE_LIMIT_MARKERS

# ponytail: marker map drives the detector — adding a provider later is a
# table row, not a new branch. Grok and Gemini are deliberately absent (both
# use their own exit-75 detectors instead of a text-marker scan).
_RATE_LIMIT_MARKERS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    ModelProvider.ANTHROPIC.value: _ANTHROPIC_RATE_LIMIT_MARKERS,
    ModelProvider.OLLAMA_CLOUD.value: _OLLAMA_RATE_LIMIT_MARKERS,
    ModelProvider.LOCAL.value: _LOCAL_RATE_LIMIT_MARKERS,
}
_OVERLOAD_MARKERS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    ModelProvider.ANTHROPIC.value: _ANTHROPIC_OVERLOAD_MARKERS,
}

# The intake (prompter) agent: a single seeded, board-adjacent interviewer.
# Unlike delivery agents it is never dispatched and runs ONE persistent
# container at a time (single CEO → one live chat). See the INTAKE section
# below and roboco/agent_sdk/intake_main.py.
INTAKE_AGENT_ID = "intake-1"

# Ambient note telling the intake agent its cwd holds clones of every project in
# the scope, so it drafts against the real trees (Grep/Glob/Read), not from memory.
_INTAKE_WORKSPACE_AMBIENT = (
    "## Workspace\n\n"
    "Your working directory holds a clone of every project in this intake scope "
    "— the primary project at your cwd, and for a multi-project scope each "
    "sibling project's clone alongside it under /data/workspaces. All are "
    "readable via Grep/Glob/Read; draft against the real trees, not from memory."
)

# The Secretary agent: a single seeded, persistent chief-of-staff container the
# CEO chats with (like intake), but with gated CEO authority. One container at a
# time. Seeded in identity.AGENTS; see roboco/agent_sdk/secretary_main.py.
SECRETARY_AGENT_ID = "secretary-1"

# Codex (OPENAI), Gemini (GEMINI), and Kimi (KIMI) are V1 delivery-roles-only
# (see roboco.llm.providers.codex / .gemini / .kimi module docstrings) —
# none supports the persistent interactive Intake/Secretary session (no
# CLI-flag equivalent to grok's --disallowed-tools/deny, no
# interactive-session driver image).
# Unlike GROK (which has its own GROK_PROMPTER_IMAGE / GROK_SECRETARY_IMAGE),
# routing any of these to Intake/Secretary would fall through to the plain
# Claude SDK-driver image with a mismatched provider env instead of refusing —
# so all three spawn paths reject it explicitly instead of silently misbehaving.
# Mirrors roboco.services.llm.INTERACTIVE_UNSUPPORTED_PROVIDERS (kept as a
# literal here to avoid a runtime import cycle; parity is pinned by a test).
# The resolver exempts interactive agents from GLOBAL/ROLE rows on these
# providers (a fleet-wide mode switch keeps the chats on Anthropic); this
# guard is the backstop for an EXPLICIT AGENT_SLUG pin, which is refused
# loudly rather than silently overridden.
_INTERACTIVE_UNSUPPORTED_PROVIDERS: tuple[ModelProvider, ...] = (
    ModelProvider.OPENAI,
    ModelProvider.GEMINI,
    ModelProvider.KIMI,
)


def _reject_interactive_unsupported_provider(
    agent_id: str, provider_type: ModelProvider
) -> None:
    """Refuse spawning the interactive Intake/Secretary agent on a delivery-
    roles-only provider. Raise BEFORE any image resolution/container mutation
    so the guarded wrapper's generic ``except Exception`` surfaces this
    cleanly on the live relay instead of the spawn silently misrouting."""
    if provider_type in _INTERACTIVE_UNSUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"{provider_type.value} is a delivery-roles-only provider (V1) — "
            f"it cannot power the interactive {agent_id} session. Route "
            f"{agent_id} to Anthropic, Grok, Ollama, or Self-Hosted instead "
            "(Mix mode's per-agent picker)."
        )


# Role -> Image mapping
# Specialized images extend the base with role-specific tools
AGENT_IMAGES: dict[str, str] = {
    # Backend
    "be-dev-1": "roboco-agent-dev-be",
    "be-dev-2": "roboco-agent-dev-be",
    "be-qa": "roboco-agent-qa-be",
    "be-pm": "roboco-agent-pm",
    "be-doc": "roboco-agent-doc",
    # Frontend
    "fe-dev-1": "roboco-agent-dev-fe",
    "fe-dev-2": "roboco-agent-dev-fe",
    "fe-qa": "roboco-agent-qa-fe",
    "fe-pm": "roboco-agent-pm",
    "fe-doc": "roboco-agent-doc",
    # UX/UI
    "ux-dev-1": "roboco-agent-ux",
    "ux-dev-2": "roboco-agent-ux",
    "ux-qa": "roboco-agent-ux",  # Uses same as dev for now
    "ux-pm": "roboco-agent-pm",
    "ux-doc": "roboco-agent-doc",
    # Board
    "main-pm": "roboco-agent-pm",
    "product-owner": "roboco-agent-pm",
    "head-marketing": "roboco-agent-pm",
    "auditor": "roboco-agent-pm",
    # PR Reviewer — read-only reviewer (diff via API, grep, post one
    # change-request; never runs code). Its own image for parity with the other
    # agents; built FROM the base, no extra toolchain. The three cell reviewers
    # are additional instances of the same role and reuse the same image (as
    # be-dev-1/-2 share one dev image) — the in-path gate adds no new image.
    "pr-reviewer-1": "roboco-agent-pr-reviewer",
    "be-pr-reviewer": "roboco-agent-pr-reviewer",
    "fe-pr-reviewer": "roboco-agent-pr-reviewer",
    "ux-pr-reviewer": "roboco-agent-pr-reviewer",
    "cell-pr-reviewer-2": "roboco-agent-pr-reviewer",
    # Intake — persistent Agent-SDK driver, not a one-shot `claude -p`.
    INTAKE_AGENT_ID: "roboco-agent-prompter",
    # Secretary — persistent Agent-SDK driver with gated CEO authority.
    SECRETARY_AGENT_ID: "roboco-agent-secretary",
}


def _qualify_agent_image(bare: str) -> str:
    """Apply the configured registry namespace + tag to a bare agent image.

    Default (no ``agent_image_registry``, no ``agent_image_tag``) returns the
    bare name unchanged — the local build flow. With a registry set the
    orchestrator spawns (and ensures) ``{registry}/roboco-agent-*[:tag]``, the
    pre-built images the release workflow publishes, instead of building.
    """
    registry = settings.agent_image_registry.rstrip("/")
    name = f"{registry}/{bare}" if registry else bare
    tag = settings.agent_image_tag
    return f"{name}:{tag}" if tag else name


def get_agent_image(agent_id: str) -> str:
    """Get the Docker image for an agent (registry-qualified when configured)."""
    return _qualify_agent_image(AGENT_IMAGES.get(agent_id, AGENT_BASE_IMAGE))


# When running in a container, we need host paths for volume mounts.
# These can be overridden via environment variables.
CLAUDE_AUTH_HOST_PATH = os.environ.get(
    "ROBOCO_HOST_CLAUDE_DIR",
    str(Path.home() / ".claude"),
)
PROJECT_HOST_PATH = os.environ.get("ROBOCO_HOST_PROJECT_DIR", "")
DATA_HOST_PATH = os.environ.get("ROBOCO_HOST_DATA_DIR", "")
# In-orchestrator path where each GROK agent's usage capture is visible. The
# agent writes <DATA_HOST_PATH>/grok-usage/<agent_id>/usage.json; the compose file
# mounts the same host dir here so the finalizer can read the captured tokens back
# (the grok analogue of reading the Claude transcript from the mounted ~/.claude).
# Override for local runs.
GROK_USAGE_DATA_DIR = os.environ.get("ROBOCO_GROK_USAGE_DIR", "/data/grok-usage")
# Same shape for CODEX agents (roboco.llm.providers.codex_cli_usage writes
# usage.json here; the finalizer reads it back — see _codex_usage_json).
CODEX_USAGE_DATA_DIR = os.environ.get("ROBOCO_CODEX_USAGE_DIR", "/data/codex-usage")

# Interactive Grok images (grok-CLI conversation drivers) — selected for the
# intake / secretary roles when their route resolves to GROK, instead of the
# Claude prompter/secretary images. Their dockerfiles build FROM roboco-agent-grok.
GROK_PROMPTER_IMAGE = "roboco-agent-grok-prompter"
GROK_SECRETARY_IMAGE = "roboco-agent-grok-secretary"
_GROK_INTERACTIVE_DOCKERFILES = {
    GROK_PROMPTER_IMAGE: "agent-grok-prompter.Dockerfile",
    GROK_SECRETARY_IMAGE: "agent-grok-secretary.Dockerfile",
}

# A one-shot Grok container exits with this code (EX_TEMPFAIL) when the run hit
# an xAI 429 (grok-cli-agent-entrypoint.sh detects it). The orchestrator parks the
# grok provider rate-limited instead of crash-retrying, breaking the
# 429 -> exit -> respawn cost loop. The probe-resume loop clears the park after
# the retry window (unknown-provider time-expiry fallback in _probe_target).
_GROK_RATE_LIMIT_EXIT_CODE = 75
_GROK_RATE_LIMIT_RETRY_AFTER_S = 60.0
# Grok has no real recovery probe (the SuperGrok OIDC token is not a valid
# bearer for the metered api.x.ai, so a probe would no-op or strand grok
# parked). The probe loop clears a grok park on a timer; the fresh agent hits
# the still-active xAI 429, exits 75, and re-parks. Back the re-park retry_after
# off exponentially within one episode so the churn dampens (60 -> 120 -> 240
# -> ... capped) instead of spinning flat. The episode gap resets the count
# once the rate limit has actually lifted.
_GROK_REPARK_BACKOFF_CAP = 4  # max 2**4 = 16x base (~16min cycle)
_GROK_REPARK_EPISODE_GAP_S = 1500.0  # 25min — > the capped ~16min cycle
# A one-shot Grok container exits with this code (EX_CONFIG) when the
# entrypoint's `grok_auth --check` backstop found the access token missing or
# expired (it can't be refreshed headlessly, so the CLI would hang at an
# interactive login prompt). Park the provider instead of crash-retrying 3x —
# the agent cannot start without a valid token, so respawning burns tokens for
# zero progress. The probe-resume loop revives the task once
# grok_auth.refresh_if_stale (run once per dispatch tick) mints a fresh token
# from the offline-access refresh token; if still expired, the next exit 78
# re-parks (no token burn). Same shape as the 429 exit-75 path (F041).
_GROK_AUTH_EXIT_CODE = 78
_GROK_AUTH_RETRY_AFTER_S = 60.0

# A one-shot Codex container exits with these SAME codes for the SAME reasons
# (its entrypoint mirrors grok's exit-code convention — see
# docker/scripts/codex-cli-agent-entrypoint.sh): 75 (EX_TEMPFAIL) on a
# detected OpenAI rate-limit / quota error, 78 (EX_CONFIG) when the
# codex_auth --check backstop finds the mounted ChatGPT-subscription token
# missing/expired. Numeric reuse is fine — the checks are scoped by
# provider_type (ModelProvider.OPENAI vs .GROK), never by exit code alone.
# Unlike grok's rate-limit park, Codex has no observed re-park storm to back
# off against yet, so this parks at a flat retry_after (no exponential
# backoff bookkeeping) — add if operators see a repark cycle in practice.
_CODEX_RATE_LIMIT_EXIT_CODE = 75
_CODEX_RATE_LIMIT_RETRY_AFTER_S = 60.0
_CODEX_AUTH_EXIT_CODE = 78
_CODEX_AUTH_RETRY_AFTER_S = 60.0

# In-orchestrator path where each GEMINI agent's usage capture is visible —
# the gemini analogue of GROK_USAGE_DATA_DIR (see there for the mount shape).
GEMINI_USAGE_DATA_DIR = os.environ.get("ROBOCO_GEMINI_USAGE_DIR", "/data/gemini-usage")

# A one-shot Gemini container exits with this code (EX_TEMPFAIL) when the run's
# captured stdout carried a quota/rate-limit error (gemini-cli-agent-
# entrypoint.sh remaps the CLI's generic exit 1 to this via
# roboco.llm.providers.gemini_cli_usage.classify_exit_code — the CLI itself has
# no dedicated exit code for this case, unlike grok's own text-grep detector).
# Same numeric value as grok's exit-75 detector (both are "try again later"),
# but checked by a SEPARATE provider-scoped predicate (_is_gemini_rate_limit_exit)
# so the two providers park independently.
_GEMINI_RATE_LIMIT_EXIT_CODE = 75
# Gemini, like grok, has no real recovery probe (an OAuth-login daily quota cap
# has no cheap API to poll remaining balance) — the probe loop clears a park on
# a timer, and a still-active quota re-parks. Back the re-park retry_after off
# exponentially within one episode so the churn dampens, mirroring
# _GROK_REPARK_BACKOFF_CAP / _GROK_REPARK_EPISODE_GAP_S exactly.
_GEMINI_REPARK_BACKOFF_CAP = 4
_GEMINI_REPARK_EPISODE_GAP_S = 1500.0
# A one-shot Gemini container exits with this code (the CLI's own dedicated
# auth-failure code, source-verified) when the entrypoint's OAuth-credential
# preflight found the mounted credential missing/empty. Unlike grok's exit 78,
# no orchestrator-side refresher daemon proactively mints a new token here —
# Google's refresh token is reusable and refreshed IN-PROCESS by the CLI itself
# (see roboco.llm.providers.gemini) — so a genuinely bad/missing credential
# re-parks flat (no exponential backoff) until an operator fixes it on the
# host, exactly like grok's own auth-exit park.
_GEMINI_AUTH_EXIT_CODE = 41

# In-orchestrator path where each KIMI agent's usage capture is visible — the
# kimi analogue of GEMINI_USAGE_DATA_DIR (see there for the mount shape).
KIMI_USAGE_DATA_DIR = os.environ.get("ROBOCO_KIMI_USAGE_DIR", "/data/kimi-usage")

# A one-shot Kimi container exits with these SAME codes for the SAME reasons
# (its entrypoint mirrors the codex/grok exit-code convention — see
# docker/scripts/kimi-cli-agent-entrypoint.sh): 75 (EX_TEMPFAIL) on a
# detected Moonshot rate-limit/quota error, 78 (EX_CONFIG) when the
# kimi_cli_config --check auth preflight finds the symlinked-in subscription
# credential missing/expired. Numeric reuse is fine — the checks are scoped
# by provider_type (ModelProvider.KIMI vs .OPENAI/.GROK), never by exit code
# alone. Flat retry_after (no exponential repark backoff, mirroring codex's
# simplicity — add backoff bookkeeping if Kimi is observed re-parking in a
# tight cycle in practice), but the retry_after itself is a tunable Settings
# field (gemini's pattern), not a hardcoded module constant — Moonshot's
# request-counted 5h quota window may want a different cadence than the flat
# 60s codex/gemini default.
_KIMI_RATE_LIMIT_EXIT_CODE = 75
_KIMI_AUTH_EXIT_CODE = 78


# =============================================================================
# ORCHESTRATOR
# =============================================================================


@dataclass(frozen=True)
class _SlaBreach:
    """Per-(role, state) SLA breach payload for _escalate_sla_breach."""

    task_id: str
    role: str
    status: str
    age_seconds: int
    sla_seconds: int


@dataclass(frozen=True)
class _IntakeRunSpec:
    """Inputs for ``_build_intake_run_cmd``, bundled to keep the signature small."""

    container_name: str
    image: str
    hosts: dict[str, str | None]
    session_id: str
    cwd: str
    cli_model: str
    api_url: str
    provider_base_url: str | None
    provider_auth_token: str | None
    provider_type: str = "anthropic"
    model: str = ""


@dataclass
class _StrategyLoopState:
    """Consecutive-failure tracking for ``_strategy_engine_loop`` (#193).

    ``failures`` counts consecutive cycle exceptions; ``notified`` gates the
    one-CEO-alert-per-episode. Both reset on the first success so a fresh
    failure episode re-notifies.
    """

    failures: int = 0
    notified: bool = False


@dataclass
class _SecretaryRunSpec:
    """Inputs for ``_build_secretary_run_cmd`` (mirrors ``_IntakeRunSpec``).

    Adds the agent uuid + HMAC token: unlike intake, the Secretary's tools call
    the backend, so the container needs an authenticated identity.
    """

    container_name: str
    image: str
    hosts: dict[str, str | None]
    session_id: str
    cwd: str
    cli_model: str
    api_url: str
    agent_uuid: str
    agent_token: str
    provider_base_url: str | None
    provider_auth_token: str | None
    provider_type: str = "anthropic"
    model: str = ""


# Roles that always work a concrete task — a spawn row with ``task_id IS NULL``
# for one of these is an unattributed-cost bug (the usage rollup can't tie the
# spend to a task). Intake (prompter), secretary, auditor, and PMs legitimately
# spawn taskless, so they are NOT flagged (#11).
_TASKLESS_SPAWN_SUSPECT_ROLES = frozenset({"developer", "qa", "documenter"})

# Statuses where `_auto_block_task` forcing "blocked" is meaningless — the
# task already moved past the caller's control to a reviewer/terminal state.
# Forcing it back to "blocked" from here would yank it out from under
# whoever now owns it instead of skipping a no-op.
_AUTO_BLOCK_SKIP_STATUSES = frozenset(
    {
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pr_review",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
        "completed",
        "cancelled",
        "blocked",
    }
)


def is_unattributed_delivery_spawn(role: str, task_id: str | None) -> bool:
    """True when a delivery-role spawn carries no ``task_id`` (#11).

    The role string comes from ``get_agent_role`` (lowercase); the comparison is
    case-insensitive for safety. Used by ``_record_spawn_session`` to warn on
    unattributed usage without noise from the intentional taskless roles.
    """
    return task_id is None and role.lower() in _TASKLESS_SPAWN_SUSPECT_ROLES


def _read_project_slug(task: dict[str, Any]) -> str | None:
    """Extract project slug from a task payload shape-tolerantly."""
    slug = task.get("project_slug")
    if slug:
        return str(slug)
    project = task.get("project") or {}
    inner = project.get("slug") if isinstance(project, dict) else None
    return str(inner) if inner else None


def _created_before(sib_created: Any, task_created: Any) -> bool:
    """True if ``sib_created`` (a datetime row value) precedes ``task_created``
    (an ISO string from the API task payload, or a datetime).

    The equal-sequence tiebreak for the merge / lane dispatch barriers: wave-
    stamped independent siblings share a sequence, so creation order decides
    who merges first. Fail-open (False) on any missing/unparseable value —
    a tie that can't be ordered must not wedge dispatch.
    """
    if sib_created is None or not task_created:
        return False
    try:
        if isinstance(task_created, str):
            task_created = datetime.fromisoformat(task_created)
        return bool(sib_created < task_created)
    except (TypeError, ValueError):
        return False


def _is_coordination_task(task: dict[str, Any]) -> bool:
    """True for a task that does no git of its own.

    Three shapes qualify: a board/fan-out coordination root (carries a product,
    no repo — its cell subtasks resolve a real project from the product's
    cell->project map), an ad-hoc per-cell map coordination root (carries a
    ``cell_projects`` map but no project/product — a multi-cell MegaTask
    root-subtask), and a MegaTask umbrella (carries a batch_id, top-level — its
    root-subtasks each carry their own branch/PR). Such a task has no
    project_slug, branch_name, or git token, and must NOT be git-gated at the
    spawn-readiness or stuck-detection checks the way a code task is. A task with
    none of project / product / cell-map / batch is genuinely unroutable and
    stays gated.
    """
    return is_branchless_coordination(
        project_id=task.get("project_id"),
        product_id=task.get("product_id"),
        batch_id=task.get("batch_id"),
        parent_task_id=task.get("parent_task_id"),
        has_cell_projects=bool(task.get("cell_projects")),
    )


# A branch is auto-created only at CLAIM (the claimed->in_progress transition).
# Before that — while a task is still pending/backlog awaiting first dispatch —
# it legitimately has no branch_name, so the readiness / stuck / spawn checks
# must NOT treat a missing branch as a defect. These are the only states where
# a code task is expected to already own a branch.
_BRANCH_EXPECTED_STATES: frozenset[str] = frozenset(
    {"claimed", "in_progress", "verifying"}
)


def _branch_is_expected(task: dict[str, Any]) -> bool:
    """True iff this task should already have a branch_name.

    A branch only exists at/after claim, and a coordination/fan-out task never
    gets one (it does no git of its own). Gating the "missing branch_name"
    readiness/stuck condition on this predicate stops the orchestrator from
    auto-blocking a never-claimed PENDING code task that simply hasn't reached
    the claim transition yet (a pending task sat 13min, auto-blocked
    every 30s, never dispatched).
    """
    if _is_coordination_task(task):
        return False
    return str(task.get("status") or "") in _BRANCH_EXPECTED_STATES


def _resolve_agent_cli_model(provider_type: str, model: str) -> str:
    """Translate an agent model name to the string Claude Code expects.

    For the Anthropic provider, short names (``opus|sonnet|haiku``) are
    translated through ``MODEL_MAP`` as they always were.  For non-Anthropic
    providers (currently Ollama Cloud) the model identifier is passed verbatim
    so raw tags like ``kimi-k2.6:cloud`` reach the Ollama-side integration
    intact.

    Extracted as a module-level function so both the ``--model`` CLI arg
    builder and the ``CLAUDE_CODE_SUBAGENT_MODEL`` env-var injector can call
    the same logic without referencing the class by name inside a staticmethod.
    """
    if provider_type == "anthropic":
        return MODEL_MAP.get(model, model)
    return model


def _agent_workspace_path(project_slug: str, team: str, agent_id: str) -> str:
    """Per-agent workspace path inside the container.

    Mirrors the bind-mount layout: the host's workspaces dir is mounted at
    /data/workspaces (orchestrator.py mount args), so each agent's clone lives
    at /data/workspaces/<project>/<team>/<agent>. Used by both
    _get_role_permissions (Edit/Write allowlist) and _build_mount_args
    (docker ``-w`` flag) so the cwd matches the allowlist scope.
    """
    return f"/data/workspaces/{project_slug}/{team}/{agent_id}"


def _agent_worktree_path(
    project_slug: str, team: str, agent_id: str, task_short_id: str
) -> str:
    """Per-task worktree path inside the container (F123).

    Each task with a branch gets its own working tree under the clone root at
    ``{clone_root}/.worktrees/{task_short_id}/`` so a coordinator PM's parallel
    roots (or a dev's parallel tasks) never clobber one shared checkout.
    """
    return (
        f"/data/workspaces/{project_slug}/{team}/{agent_id}/.worktrees/{task_short_id}"
    )


def _agent_cwd_path(
    project_slug: str,
    team: str,
    agent_id: str,
    git_context: SpawnGitContext | None,
) -> str:
    """The container cwd + Edit/Write scope for a workspace role (F123).

    A task carrying a branch edits in its per-task worktree; a branchless or
    no-task spawn stays at the clone root. ONE formula shared by
    ``_append_workspace_cwd`` (docker ``-w``) and ``_get_role_permissions``
    (Edit/Write allowlist via ``_prepare_agent_spawn``) so the cwd and the
    allowlist scope can never drift to different paths.
    """
    clone_root = _agent_workspace_path(project_slug, team, agent_id)
    if git_context and git_context.task_short_id:
        return _agent_worktree_path(
            project_slug, team, agent_id, git_context.task_short_id
        )
    return clone_root


def _cell_workspace_path(project_slug: str, team: str) -> str:
    """Cell-level workspace path (documenter scope).

    Same rationale as ``_agent_workspace_path``; documenters work at the cell
    branch, not a per-agent dev branch.
    """
    return f"/data/workspaces/{project_slug}/{team}"


def _resolve_project_slug_from_git_context(
    git_context: "SpawnGitContext | None",
) -> str:
    """Extract project_slug from git_context, falling back to 'default'.

    Module-level counterpart to the instance method ``_resolve_project_slug``.
    Called by static / classmethod contexts (e.g. ``_build_mount_args``) that
    cannot access ``self``. The fallback warning is omitted here because the
    instance method already logs it when the full spawn path runs; this helper
    is only for the mount-args path where the agent_id/task_id context is not
    available.
    """
    if git_context and git_context.project_slug:
        return git_context.project_slug
    return "default"


# =============================================================================
# SPAWN MANIFEST — per-developer tool manifest mounting (Phase 1)
# =============================================================================

# Phase 4: every spawned role gets a gateway manifest. The legacy briefing path
# is gone. A role omitted here gets NO manifest and ROBOCO_GATEWAY_ENABLED=false,
# i.e. none of its flow verbs are pre-registered — so it can never claim its work
# and the dispatcher respawns it on the same task forever. The only roles that
# may be absent are the human-only ones (prompter, secretary) that the
# orchestrator never spawns as delivery agents.
GATEWAY_ENABLED_ROLES: frozenset[str] = frozenset(
    {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "pr_reviewer",
    }
)


def _build_manifest_for_agent(
    agent_id: str, model: str, workspace_path: str | None = None
) -> Path | None:
    """Write a SpawnManifest for developer-role agents; return the host path.

    Returns ``None`` for roles outside ``GATEWAY_ENABLED_ROLES`` so callers
    can skip the manifest mount entirely without extra branching.

    Args:
        agent_id: Agent slug (e.g. ``be-dev-1``).
        model:    Resolved model name passed to ``SpawnInputs.agent_model``.
        workspace_path: The task-resolved workspace (project clone or per-task
            worktree) — the SAME path the container ``-w`` uses. Without it
            the manifest falls back to the agent's roboco-project workspace,
            which is WRONG for any other project's task (live 2026-07-02:
            be-dev-2's manifest pointed at /data/workspaces/roboco while the
            task lived in guard-core-saas-backend).

    Returns:
        Absolute host path to the written JSON file, or ``None``.
    """
    from uuid import UUID

    from roboco.runtime.spawn_manifest import (
        SpawnInputs,
        build_for_role,
        write_manifest,
    )

    role = get_agent_role(agent_id) or "developer"
    if role not in GATEWAY_ENABLED_ROLES:
        return None

    team = get_agent_team(agent_id) or "backend"
    # UUID for the agent comes from the seeded AGENT_UUIDS map (slug -> UUID
    # string).  Fall back to uuid4 for unknown agents so the function stays
    # callable in tests without seeded data.
    raw_uuid = AGENT_UUIDS.get(agent_id)
    agent_uuid = UUID(raw_uuid) if raw_uuid else __import__("uuid").uuid4()

    resolved_workspace = (
        Path(workspace_path)
        if workspace_path
        else Path(settings.workspaces_root) / "roboco" / team / agent_id
    )

    manifest = build_for_role(
        SpawnInputs(
            agent_id=agent_uuid,
            role=role,
            team=team,
            workspace_path=resolved_workspace,
            agent_model=model,
        )
    )

    # Two paths in play:
    #   - orchestrator-internal: where the file is written inside the
    #     orchestrator container (settings.manifest_host_dir). The compose
    #     volume mount makes this dir visible on the host.
    #   - host-side: what the docker daemon needs for the bind-mount into
    #     the spawned agent. Computed via DATA_HOST_PATH translation.
    write_dir = Path(settings.manifest_host_dir)
    write_path = write_dir / f"{agent_id}.json"
    write_manifest(manifest, write_path)
    if DATA_HOST_PATH:
        return Path(f"{DATA_HOST_PATH}/manifests/{agent_id}.json")
    return write_path


class AgentReadinessError(Exception):
    """Raised when spawn_agent refuses to spawn because the task isn't ready.

    The pre-flight gate auto-blocks the offending task before raising, so the
    dispatcher doesn't keep retrying. Callers should log and move on.
    """


class _SpawnAbortedDuringShutdown(Exception):
    """Raised when a non-blocking intake/secretary spawn completes ``docker run``
    after the orchestrator began shutting down.

    The raiser has already removed the just-started container (so it isn't
    orphaned); the guarded wrapper catches this BEFORE its generic
    ``except Exception`` and closes the live relay silently — shutdown is not a
    user-facing failure, so no error is pushed to the SSE stream. The F070
    ``stop()`` drain awaits the bg spawn coroutine, so this surfaces cleanly
    instead of the registration landing a live container into a registry that
    ``stop()`` has already finished iterating.
    """


def _is_held_ceo_source(task: dict[str, Any]) -> bool:
    """True for sources the PM dispatcher must never route as delivery work.

    External-PR review (owned by the PR dispatcher), release proposals, X
    posts/replies, and video-post drafts (all CEO-HELD, acted on only by
    their own routes), and a self-heal fix task until the CEO's
    approve_and_start flips ``confirmed_by_human``. A ``vault_note`` draft is
    NOT held: it is a board-assigned intake draft, so this dispatcher's board
    branch routes it to the board review (its start gate is the CEO's
    approve_and_start, like any board-routed draft). Module-level (not a
    method) so the dispatcher's unit tests, which drive it with a
    wholesale-mocked ``self``, exercise the real skip logic rather than an
    auto-mocked stub.
    """
    source = task.get("source")
    if source in PR_REVIEW_SOURCES:
        return True
    if source == RELEASE_MANAGER_SOURCE:
        return True
    if source in X_SOURCES:
        return True
    if source in VIDEO_HELD_SOURCES:
        return True
    return source == SELF_HEAL_SOURCE and not task.get("confirmed_by_human")


def _is_branch_pending(task: dict[str, Any]) -> bool:
    """A supersede umbrella whose branch cut is still in progress or failed.

    The dispatcher must NOT route it to Main PM until the background branch
    cut completes and clears the marker. ``branch_cut_failed`` (set after a
    failed cut, kept through the retry backoff or after a CEO unblock of a
    BLOCKED umbrella) is treated the same way: the sweep re-runs the cut and
    clears the marker on success. ``orchestration_markers`` is carried on the
    task dict from ``task_to_response`` so the dispatcher sees it.
    """
    om = task.get("orchestration_markers")
    if not isinstance(om, dict):
        return False
    return bool(om.get("branch_pending")) or bool(om.get("branch_cut_failed"))


def _is_non_dev_dispatch_source(task: dict[str, Any]) -> bool:
    """Sources ``_dispatch_dev_work`` must skip: every CEO-held source plus the
    Board exploration cycles (``board_roadmap`` / feature-spotlight exploration
    / ``board_pest_control`` / ``board_periscope`` / ``board_coroner`` /
    ``board_sentinel`` / ``board_spackle`` / ``board_scales`` /
    ``board_mirror`` / ``board_megaphone`` / ``board_librarian`` /
    ``board_war_room`` / ``board_barfly`` / ``board_dogfood``) that
    ``_dispatch_pm_work`` owns. One flat call keeps the dev loop's skip out
    of a long per-source ``if`` chain (xenon budget)."""
    if _is_held_ceo_source(task):
        return True
    return task.get("source") in (
        ROADMAP_SOURCE,
        X_FEATURE_EXPLORATION_SOURCE,
        PEST_CONTROL_SOURCE,
        PERISCOPE_SOURCE,
        CORONER_SOURCE,
        SENTINEL_SOURCE,
        SPACKLE_SOURCE,
        SCALES_SOURCE,
        MIRROR_SOURCE,
        MEGAPHONE_SOURCE,
        LIBRARIAN_SOURCE,
        WAR_ROOM_SOURCE,
        BARFLY_SOURCE,
        DOGFOOD_SOURCE,
    )


async def _dispatch_board_program_exploration(orch: Any, task: dict[str, Any]) -> bool:
    """Route an assigned pending task to its Board Program's one-shot
    exploration dispatcher, keyed by ``task['source']``. Every registered
    program (roadmap / x_feature / pest_control / periscope / coroner /
    sentinel / spackle / scales / mirror / megaphone / librarian /
    war_room / barfly / dogfood) is solo-authored (PO/HoM/Auditor alone) —
    this bypasses the two-reviewer board-review gate; none of these ever ride
    ``_handle_board_assigned_task`` (that would also spawn a second board
    role and fire the Approve & Start handoff, both wrong for a cycle with
    one author). Module-level (not a method, so it always dispatches to the
    REAL per-source method — a stub that mocks only the individual
    ``_dispatch_*_exploration`` attributes, as every board-program dispatch
    test does, is unaffected), mirroring ``_is_non_dev_dispatch_source``. A
    dict dispatch table, not an ``if``/``elif`` chain, keeps
    ``_dispatch_pm_work``'s own cyclomatic complexity bounded as new
    programs register (xenon budget).

    Returns True when handled — the caller must not fall through to the
    board-review / PM-assigned paths — False for a normal PM/board task.

    A ``board_programs``-scope maintenance pause leaves a matched task
    PENDING and untouched (still returns True; it must never fall through
    to the two-reviewer board-review path, which is the wrong flow for a
    solo-authored program task) rather than dispatching the explorer spawn.
    """
    dispatch: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
        ROADMAP_SOURCE: orch._dispatch_roadmap_exploration,
        X_FEATURE_EXPLORATION_SOURCE: orch._dispatch_feature_spotlight_exploration,
        PEST_CONTROL_SOURCE: orch._dispatch_pest_control_exploration,
        PERISCOPE_SOURCE: orch._dispatch_periscope_exploration,
        CORONER_SOURCE: orch._dispatch_coroner_exploration,
        SENTINEL_SOURCE: orch._dispatch_sentinel_exploration,
        SPACKLE_SOURCE: orch._dispatch_spackle_exploration,
        SCALES_SOURCE: orch._dispatch_scales_exploration,
        MIRROR_SOURCE: orch._dispatch_mirror_exploration,
        MEGAPHONE_SOURCE: orch._dispatch_megaphone_exploration,
        LIBRARIAN_SOURCE: orch._dispatch_librarian_exploration,
        WAR_ROOM_SOURCE: orch._dispatch_war_room_exploration,
        BARFLY_SOURCE: orch._dispatch_barfly_exploration,
        DOGFOOD_SOURCE: orch._dispatch_dogfood_exploration,
    }
    source = task.get("source")
    handler = dispatch.get(source) if isinstance(source, str) else None
    if handler is None:
        return False
    from roboco.services.maintenance_pause import PauseScope

    if await orch._is_paused(PauseScope.BOARD_PROGRAMS):
        return True
    await handler(task)
    return True


# Cap on findings rendered inline in a dispatch prompt (REVISION_REQUIRED /
# the PM bounced-block) — beyond this, an overflow line points at evidence().
_PROMPT_FINDINGS_CAP = 10


def _render_open_finding_prompt_line(row: Any) -> str:
    """One ``task_review_findings`` row -> a single dispatch-prompt line.
    Module-level (not a method), mirroring ``_is_non_dev_dispatch_source``."""
    loc = row.file or "—"
    if row.line is not None:
        loc += f":{row.line}"
    fix = f" → {row.fix}" if row.fix else ""
    return f"[F-{str(row.id)[:8]}] {loc} — {row.expected} → {row.actual}{fix}"


# Bounded retry for the video render loop — single source of truth lives in
# the policy/markers layer (importable from API code without importing this
# orchestrator module); mirrored here under the historical name.
_MAX_VIDEO_RENDER_ATTEMPTS = _markers.MAX_VIDEO_RENDER_ATTEMPTS


def _format_seen_features(markers_dict: dict[str, Any]) -> str:
    """Render the seen-features ledger with dates when the enriched
    ``x_spotlight_brief`` marker carries them, falling back to the plain
    slug list (a pre-brief exploration, or a brief-gather failure) — the
    prompt must never break on a missing/partial marker. Module-level (not a
    method), mirroring ``_is_non_dev_dispatch_source``, so this is unit
    testable without a wholesale-mocked ``self`` (xenon budget: keeps
    ``_build_feature_spotlight_prompt`` a flat render instead of inlining
    this branching)."""
    brief = markers_dict.get(_markers.X_SPOTLIGHT_BRIEF) or {}
    seen = brief.get("seen")
    if isinstance(seen, list) and seen:
        return ", ".join(
            f"{s.get('slug')} (seen {str(s.get('seen_at') or '')[:10]})" for s in seen
        )
    slugs = markers_dict.get(_markers.X_SEEN_FEATURES) or []
    return ", ".join(slugs) if slugs else "(none yet — this is the first cycle)"


def _format_shipped_since(markers_dict: dict[str, Any]) -> str:
    """Render the CHANGELOG sections shipped since the last spotlight
    activity — empty/missing brief renders as "nothing new" rather than
    breaking the prompt (see ``_format_seen_features``)."""
    brief = markers_dict.get(_markers.X_SPOTLIGHT_BRIEF) or {}
    shipped = brief.get("shipped_since")
    if not isinstance(shipped, list) or not shipped:
        return "(nothing new since the last cycle, per CHANGELOG.md)"
    parts = [
        f"v{entry.get('version')} ({entry.get('date')}): "
        f"{', '.join(entry.get('titles') or []) or 'no subsections'}"
        for entry in shipped
    ]
    return "; ".join(parts)


def _format_rejected_spotlights(markers_dict: dict[str, Any]) -> str:
    """Render recently CEO-rejected x_feature drafts + their reasons, so HoM
    steers away from ground the CEO already turned down."""
    brief = markers_dict.get(_markers.X_SPOTLIGHT_BRIEF) or {}
    rejected = brief.get("rejected")
    if not isinstance(rejected, list) or not rejected:
        return "(none)"
    return "; ".join(
        f"{r.get('title') or r.get('slug')} — {r.get('reason')}" for r in rejected
    )


def _format_barfly_candidates(markers_dict: dict[str, Any]) -> str:
    """Render Barfly's screened candidate conversations for the exploration
    prompt — module-level (not a method), mirroring ``_format_seen_features``,
    so it's unit testable without a wholesale-mocked ``self``. A missing/
    malformed marker renders as "(none)" rather than breaking the prompt."""
    candidates = markers_dict.get(_markers.BARFLY_CANDIDATES)
    if not isinstance(candidates, list) or not candidates:
        return "(none)"
    return "\n".join(
        f"- id={c.get('id')} author={c.get('author_handle')}: "
        f"{c.get('text')} ({c.get('engagement_note')})"
        for c in candidates
        if isinstance(c, dict)
    )


from roboco.runtime.engines._shared import SharedEngine
from roboco.runtime.engines.ci_watch import CiWatchEngine
from roboco.runtime.engines.dep_update import DepUpdateEngine
from roboco.runtime.engines.dispatch_breaker import DispatchBreakerEngine
from roboco.runtime.engines.dispatch_claim import DispatchClaimEngine
from roboco.runtime.engines.dispatch_prompts import DispatchPromptsEngine
from roboco.runtime.engines.dispatch_routing import DispatchRoutingEngine
from roboco.runtime.engines.dispatch_work import DispatchWorkEngine
from roboco.runtime.engines.env_sync import EnvSyncEngine
from roboco.runtime.engines.interactive_sessions import InteractiveSessionsEngine
from roboco.runtime.engines.rate_limit_probe import RateLimitProbeEngine
from roboco.runtime.engines.reconcile import ReconcileEngine
from roboco.runtime.engines.release_manager import ReleaseManagerEngine
from roboco.runtime.engines.spawn_config import SpawnConfigEngine
from roboco.runtime.engines.spawn_exit import SpawnExitEngine
from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine
from roboco.runtime.engines.strategy import StrategyEngine
from roboco.runtime.engines.sweeps import SweepsEngine
from roboco.runtime.engines.telegram_poll import TelegramPollEngine
from roboco.runtime.engines.vault import VaultEngine
from roboco.runtime.engines.video_render import VideoRenderEngine
from roboco.runtime.engines.x_mentions import XMentionsEngine


class AgentOrchestrator(
    SharedEngine,
    CiWatchEngine,
    DepUpdateEngine,
    DispatchBreakerEngine,
    DispatchClaimEngine,
    DispatchPromptsEngine,
    DispatchRoutingEngine,
    DispatchWorkEngine,
    EnvSyncEngine,
    InteractiveSessionsEngine,
    RateLimitProbeEngine,
    ReconcileEngine,
    ReleaseManagerEngine,
    SpawnConfigEngine,
    SpawnExitEngine,
    SpawnLaunchEngine,
    StrategyEngine,
    SweepsEngine,
    TelegramPollEngine,
    VaultEngine,
    VideoRenderEngine,
    XMentionsEngine,
):
    """
    Manages Claude Code containers for all agents.

    Responsibilities:
    - Spawn agents as Docker containers
    - Monitor health via docker inspect
    - Handle waiting states and respawning
    - Provide status API
    - Cost-efficient on-demand spawning
    """

    # Per-agent-slug lock serializing ensure_sandbox's check-cache -> provision
    # -> store section. Declared here (not in __init__, to keep its statement
    # count under the gate) and lazily allocated on first use.
    _sandbox_locks: dict[str, asyncio.Lock]
    # Expected-stop breadcrumbs (agent_id -> (reason, monotonic ts)); lazily
    # allocated by _record_expected_stop, same statement-budget rationale.
    _expected_stops: dict[str, tuple[str, float]]
    # Agent slug -> (task_id, monotonic deadline) for a claim/spawn in flight.
    # Declared here (same statement-budget rationale) - __new__ below is the
    # single init site covering both the real constructor and every bare
    # __new__() test double; _is_claim_in_flight's getattr guard covers the
    # rarer bare object.__new__() bypass.
    _claims_in_flight: dict[str, tuple[str, float]]
    # Declared here (not inline in __init__, same statement-budget rationale)
    # so the two auth-notification sets can init in a single __init__
    # statement below.
    _auth_ceo_notified: set[str]
    _auth_parked_agents: set[str]
    # Last time the auditor was spawned, by reactive alert or scheduled sweep.
    # Drives the ROBOCO_AUDIT_INTERVAL_SECONDS throttle. Per-instance override.
    _last_audit_spawn_at: datetime | None = None
    # Fleet active-time clock (roboco/services/uptime.py), refreshed by
    # _refresh_uptime at most every UPTIME_REFRESH_SECONDS. None until the
    # first load (or a bare __new__ test double) - _active_age then falls
    # back to plain wall-clock elapsed, so every path degrades to
    # pre-ledger behaviour.
    _uptime: "UptimeLedger | None" = None
    _uptime_loaded_at: datetime | None = None

    def __new__(cls, *_args: Any, **_kwargs: Any) -> "AgentOrchestrator":
        """Allocate the instance and pre-initialize lazy dispatcher state.

        ``__init__`` still re-initializes ``_instances``; this just guarantees
        the attributes exist for tests that bypass ``__init__`` via bare
        ``AgentOrchestrator.__new__(AgentOrchestrator)``.

        Auditor-dispatch paths read ``_last_audit_spawn_at`` and
        ``_notification_spawn_at`` from partially-constructed instances, so
        they must be present here; the per-instance cooldown stores are
        re-initialized by ``__init__`` when the normal constructor runs.
        """
        instance = super().__new__(cls)
        instance._instances = {}
        instance._last_audit_spawn_at = None
        instance._notification_spawn_at = {}
        instance._notification_spawn_count = {}
        instance._claims_in_flight = {}
        return instance

    def __init__(
        self,
        mcp_config_dir: Path | None = None,
        project_root: Path | None = None,
        dispatcher_interval: int = 30,
    ):
        self.mcp_config_dir = mcp_config_dir or Path(".mcp")
        self.project_root = project_root or Path.cwd()
        self.dispatcher_interval = dispatcher_interval

        self._instances: dict[str, AgentInstance] = {}
        # Sandboxed per-agent-spawn DB/Redis provisioner. Network is threaded
        # through explicitly (rather than the provisioner importing
        # AGENT_NETWORK itself) so a future network-isolation change only
        # has to flip this constant here — sandboxes ride along.
        self._sandbox = SandboxProvisioner(network=AGENT_NETWORK)
        # On-demand sandbox creds cache (agent slug -> last-provisioned info),
        # consulted by ensure_sandbox / request_sandbox. Evicted at teardown
        # and by the janitor sweep. Known ceiling: in-memory only — an
        # orchestrator restart forgets it; the next request_sandbox call
        # re-provisions (provision()'s pre-clear tears down any stale
        # container) with fresh creds.
        self._sandbox_info: dict[str, SandboxInfo] = {}
        # Gateway-health grace tracker: agent slug -> first time its gateway was
        # seen broken. Tolerates a transient probe miss before the reaper recovers
        # a broken-but-alive agent (see _maybe_recover_broken_gateway).
        self._gateway_broken_since: dict[str, datetime] = {}
        self._waiting_records: dict[str, WaitingRecord] = {}
        # #71: a resumed agent's WaitingRecord is torn down only once liveness is
        # confirmed (not on a bare launch) — a container that launches then dies
        # immediately would otherwise strand its task until the reaper's TTL.
        self._resume_confirm_delay: float = 30.0
        self._health_task: asyncio.Task | None = None
        self._dispatcher_task: asyncio.Task | None = None
        self._sweeper_task: asyncio.Task | None = None
        # Last time the transcript-retention prune ran (throttled in the sweep).
        self._last_transcript_prune: datetime | None = None
        self._last_image_prune: datetime | None = None
        # Rate-limit probe loop: 30-second interval, scans Redis for all
        # rate-limited providers and resolves waiting agents on success.
        self._init_engine_loop_task_slots()
        # per-engine-loop heartbeat (monotonic last-success, interval) so
        # _check_loop_liveness can alert when a cycle task dies silently.
        self._loop_heartbeats: dict[str, tuple[float, float]] = {}
        # Provider registry: maps a ModelProvider to a dedicated AgentProvider
        # backend. Only providers needing a non-Claude-Code runtime are
        # registered (currently GROK, which speaks the OpenAI protocol). Agents
        # on unregistered providers (Anthropic / Ollama Cloud / self-hosted) use
        # the built-in _spawn_container path unchanged. Built lazily.
        self._provider_registry: ProviderRegistry | None = None
        # Tracks which providers have already received a CEO notification
        # during the current rate-limit episode.  Cleared when the probe
        # succeeds and the rate limit is lifted (tracker.clear() path).
        self._rate_limit_ceo_notified: set[str] = set()
        # Same one-shot-per-episode shape for the host Claude Code OAuth
        # expiry: _auth_ceo_notified is discarded when an agent that was
        # itself auth-parked (tracked in _auth_parked_agents) later exits
        # gracefully - proof the credential is back. An unrelated agent's
        # graceful exit must NOT clear it. One statement (both class-level
        # annotated above) to stay under the __init__ statement budget.
        self._auth_ceo_notified, self._auth_parked_agents = set(), set()
        # Strong refs for fire-and-forget audit writes. Without this, the
        # event loop only weak-refs the Task and may GC it before it
        # commits — audit_log was silently empty because of this.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # Wake-up signal for the dispatcher. Set() by API routes immediately
        # after status transitions so the dispatcher reacts in milliseconds
        # instead of waiting for the next 30-second tick.
        self._dispatch_wake: asyncio.Event = asyncio.Event()
        self._running = False
        # Set True once stop() completes — makes the (lifespan + bootstrap
        # safety-net) double-call a clean no-op instead of re-stopping already
        # stopped agents / re-draining an empty bg-task set.
        self._stopped = False
        self._lock = asyncio.Lock()
        # Serializes CEO supersede calls so a double-click can't pass the
        # find_supersede_umbrella dedup check twice and cut two branches /
        # spawn two umbrellas for the same PR (the check is read-then-write
        # with no DB-level uniqueness).
        self._supersede_lock = asyncio.Lock()
        # In-flight branch cuts (umbrella id strings) so the reconciliation
        # sweep does not double-spawn a second _cut_supersede_branch for an
        # umbrella whose first cut is still running (~90-390s). Cleared in
        # _cut_supersede_branch's finally block.
        self._supersede_cuts_in_flight: set[str] = set()
        # Serialize concurrent live-chat starts for the single-id interactive
        # agents (intake / secretary). Each has a fixed agent id, so two
        # concurrent starts race on the container name (``docker run --name
        # roboco-agent-<id>``) and the ``_instances[<id>]`` write — orphaning a
        # container + relay. The lock makes the second start wait for the first
        # to fully register (so the second's reap-prior step sees it) instead of
        # both clobbering the registry. Distinct from ``self._lock`` (which
        # ``stop_agent`` takes) to avoid a reentrancy deadlock: the spawn body
        # holds this lock then calls ``stop_agent`` (acquires ``self._lock``) —
        # lock order is always ``_intake_spawn_lock`` -> ``self._lock``, never
        # the reverse, so there's no cycle.
        self._intake_spawn_lock = asyncio.Lock()
        self._secretary_spawn_lock = asyncio.Lock()
        # Per-tick set of task_ids already handled by an earlier
        # dispatcher. Reset at the start of every _dispatch_all_work.
        # Consumed via `self._mark_task_handled` / `_is_task_handled`.
        self._tick_handled_tasks: set[str] = set()
        # Respawn circuit breaker: per (agent_slug, task_id), tracks how
        # many times we've spawned without the task status changing. A PM
        # that gets re-spawned on the same pending task with no progress
        # is in a loop — without this gate the orchestrator re-spawns every
        # tick forever (seen in production on 2026-04-22).
        self._pm_respawn_tracker: dict[tuple[str, str], dict[str, Any]] = {}
        # _claims_in_flight is (re)initialized by __new__ above, not here,
        # see its class-level declaration for the statement-budget rationale;
        # _select_agent_for_cell treats an unexpired entry like an active
        # agent so a second pending task never stacks a claim onto the same
        # not-yet-active agent while its claim/branch-creation is still in
        # flight (the 2026-09-05 triple-claim lock-convoy amplifier).
        # In-path PR-gate CI-status cache: (project_slug, pr_number) ->
        # (monotonic fetch time, state). Bounds get_pr_ci_status calls to
        # roughly one per _GATE_CI_STATUS_CACHE_TTL_SECONDS per PR instead
        # of one per dispatch tick per task (see _gate_task_ci_pending).
        self._gate_ci_status_cache: dict[tuple[str, int], tuple[float, str | None]] = {}
        # Dispatcher heartbeat throttle (see _emit_dispatcher_heartbeat).
        self._last_dispatch_heartbeat: datetime | None = None
        # Serializes the fire-and-forget respawn-tracker upserts so same-key
        # persists COMMIT in schedule (logical) order — not whatever order their
        # DB transactions resolve in. A respawn loop fires count 1->2->3->4 in
        # quick succession, one fire-and-forget persist per increment; without
        # serialization a slow stale persist (count=2) can commit AFTER a fast
        # fresh one (count=4), leaving the durable row at the stale low count
        # and re-burning the strike threshold on restart. The lock is acquired
        # as the FIRST await in _persist_respawn_record, so acquisition order
        # matches task creation order (FIFO ready queue), which is the logical
        # schedule order. Persists are best-effort background writes, so
        # serializing them never blocks the dispatcher hot path (the lock lives
        # in the bg task, not the caller).
        self._respawn_persist_lock = asyncio.Lock()
        # Board agents (Product Owner / Head of Marketing) get exactly ONE
        # review pass per assigned task: they have no verb to claim, plan,
        # delegate, or complete, so a respawn cannot advance the task and would
        # just loop. Tracks (agent_slug, task_id) already dispatched.
        #
        # Scope is the two-reviewer board REVIEW pass (plus vault curation's
        # same-process race guard, which has its own durable marker behind it)
        # — NOT the solo Board Program exploration cycles. Those used it too
        # until 2026-07-26, and because the set never expires and lives only in
        # memory, an exploration whose propose verb rejected was never retried
        # for the life of the process: Periscope, Sentinel, Scales and Barfly
        # each spawned once, failed, and sat PENDING until a restart. They now
        # rely on ``_pm_respawn_should_gate`` alone, which is the guard that
        # actually bounds a loop — DB-persisted, reset by a status change, and
        # cooled down so a deploy that fixes the cause lets the work resume.
        self._board_dispatched: set[tuple[str, str]] = set()
        # Cross-tick damper for notification-triggered spawns (escalation /
        # approval / audit / a2a). Those dispatchers carry no task_id, so the
        # readiness gate and the PM respawn breaker never see them — without
        # this, an unacknowledged notification respawns its recipient every
        # dispatch tick, unbounded. One spawn per (agent, notification) per
        # cooldown window; the notification stays pending, so the next window
        # retries if it is still unacked. In-memory by design (a restart just
        # allows one immediate retry — a tick damper, not durable state).
        self._notification_spawn_at: dict[tuple[str, str], float] = {}
        # Hard-cap counter companion to _notification_spawn_at: spawns per
        # (agent, notification) so a wedged escalation stops respawning its
        # target past notification_spawn_max_attempts (the no-task_id analogue
        # of the PM respawn breaker). In-memory (a restart allows a fresh run).
        self._notification_spawn_count: dict[tuple[str, str], int] = {}
        # Cluster C5: a board review is a two-reviewer gate — BOTH the Product
        # Owner and the Head of Marketing must review a board/coordination task
        # before it is handed to the CEO for Approve & Start. Once both have
        # finished (dispatched-and-no-longer-active), the orchestrator emits ONE
        # formal CEO notification per task. Tracks task_ids already notified so
        # the signal fires exactly once.
        self._board_review_ceo_notified: set[str] = set()
        # Stale-claim reaper config, sourced from
        # stale_claim_reap_seconds (default 600) rather than
        # claim_stale_seconds (default 180) — the reaper gets the longer
        # window of the two claim-staleness thresholds.
        # Smoke run 3 showed agents reaped at 180s while actively retrying
        # rejected verbs — LLM inference routinely exceeds that window.
        # Tests bypass `__init__` via `__new__` and set _claim_heartbeat_ttl
        # directly; production never uses _task_svc from __init__.
        self._claim_heartbeat_ttl: int = settings.stale_claim_reap_seconds
        # Short debounce for closure respawn of a recently-paused parent —
        # NOT the reaper window. See _is_recently_paused.
        self._closure_recently_paused_ttl: int = (
            settings.pm_closure_recently_paused_seconds
        )
        # Longer threshold before a wedged (ACTIVE-yet-idle) GROK container is
        # killed + evicted so the reaper can release its task; see
        # _maybe_kill_wedged_grok.
        self._grok_idle_kill_ttl: int = settings.grok_idle_kill_seconds
        # #73: a non-GROK agent stuck in a non-verb loop (alive, no heartbeat
        # advance) is killed past this longer window so the reaper can release
        # its task; see _maybe_kill_stuck_claude.
        self._claude_stuck_kill_ttl: int = settings.claude_stuck_kill_seconds
        # Cost ceiling (USD) before a live GROK container is killed — the budget
        # kill-switch parity (the grok CLI exposes no live usage hook). 0 disables.
        # See _enforce_grok_cost_budget.
        self._grok_max_cost_usd: float = settings.grok_max_cost_usd
        # Grok re-park backoff state. Track the re-park count within one episode
        # so retry_after can back off exponentially (dampening the ~90s
        # crash-retry churn), and the last park time so a gap (the rate limit
        # actually lifted) resets the count for the next episode.
        self._grok_last_park_at: datetime | None = None
        self._grok_repark_count: int = 0
        # Gemini re-park backoff state — same shape as grok's above, tracked
        # separately so the two providers' rate-limit episodes never interfere.
        self._gemini_last_park_at: datetime | None = None
        self._gemini_repark_count: int = 0
        # Configurable retry_after base for GEMINI parks (operators may want to
        # tune these for Google's own OAuth-quota reset cadence, unlike grok's
        # hardcoded equivalents — see settings.gemini_rate_limit_retry_after_seconds).
        self._gemini_rate_limit_retry_after_s: float = (
            settings.gemini_rate_limit_retry_after_seconds
        )
        self._gemini_auth_retry_after_s: float = (
            settings.gemini_auth_retry_after_seconds
        )
        # Configurable retry_after base for KIMI parks (gemini's tunable
        # pattern, not codex's hardcoded module constants — see
        # _KIMI_RATE_LIMIT_EXIT_CODE). No repark-backoff bookkeeping (codex's
        # simplicity): add it if Kimi is observed re-parking in a tight cycle.
        self._kimi_rate_limit_retry_after_s: float = (
            settings.kimi_rate_limit_retry_after_seconds
        )
        self._kimi_auth_retry_after_s: float = settings.kimi_auth_retry_after_seconds

    def _init_engine_loop_task_slots(self) -> None:
        """Task handles for the default-off engine loops. Split out of
        __init__ (rather than inlined) to keep it under the statement budget
        as more engine loops accrete over time."""
        self._rate_limit_probe_task: asyncio.Task | None = None
        self._strategy_engine_task: asyncio.Task | None = None
        self._external_pr_poll_task: asyncio.Task | None = None
        self._self_heal_task: asyncio.Task | None = None
        self._ci_watch_task: asyncio.Task | None = None
        self._dep_update_task: asyncio.Task | None = None
        self._env_sync_task: asyncio.Task | None = None
        self._release_manager_task: asyncio.Task | None = None
        self._x_mentions_task: asyncio.Task | None = None
        self._board_program_task: asyncio.Task | None = None
        self._video_render_task: asyncio.Task | None = None
        self._vault_intake_task: asyncio.Task | None = None
        self._vault_janitor_task: asyncio.Task | None = None
        self._vault_kb_task: asyncio.Task | None = None
        self._telegram_poll_task: asyncio.Task | None = None

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the orchestrator.

        Single gate for ROBOCO_ROLE=api: routes still use this instance for
        isolated on-demand actions with no fleet-state dependency (see
        roboco/api/deps.py; e.g. supersede_external_pr's branch cut) - the
        secretary/intake live chats route to the dispatcher instead (see
        docker/nginx.conf) - but the fleet-wide background loops and the
        once-per-fleet startup reconciliation belong to the dispatcher role,
        so they must not also run here. 'dispatcher' and 'all' fall through
        unchanged.
        """
        self._running = True
        if settings.role == "api":
            logger.info("Orchestrator attached (api role: background loops skipped)")
            return
        await self._mark_running_and_beat()

        # Ensure agent image is built
        await self._ensure_agent_image()

        # Restore any WaitingRecord rows left by a prior orchestrator run so
        # agents that were WAITING_LONG at shutdown can still be resolved.
        await self.restore_waiting_records()

        # Restore the PM-respawn loop counter so a task wedged at the strike
        # threshold trips immediately after a restart instead of resetting to
        # count=1 and re-burning the whole budget. Validates against live tasks
        # (drops terminal/missing rows); inert when the table is empty.
        await self.restore_respawn_tracker()

        # Self-heal: roll back orphan claims left over from a prior crash.
        # Tasks that show CLAIMED/IN_PROGRESS but have NO
        # branch_name set indicate _apply_claim_fields committed the claim
        # (status + claimant fields) before _provision_claim's branch
        # creation committed (or before claim-rollback was atomic). Without
        # this, the next claim attempt fails non-idempotent on `git checkout -b`.
        await self._reconcile_orphan_claims_on_startup()

        # Re-adopt agent containers that survived this orchestrator restart, so
        # the spawn gate + reaper see them as live immediately (no double-spawn,
        # no over-reap). Inert when nothing is running. Must run before the
        # dispatcher/reaper loops launch below.
        await self._heal_stale_agent_tokens()
        await self._readopt_running_agents()

        # Close agent_spawn_sessions rows left open by a prior orchestrator
        # crash so usage/cost rollups (which filter ended_at IS NOT NULL) count
        # their tokens. Running agents stay open for their live finalize.
        await self._reconcile_orphan_spawn_sessions()

        # Orphan sandbox sweep: a sandbox whose owning agent container didn't
        # survive the restart (or a prior crash mid-teardown) is removed here
        # rather than lingering until its next reaper-tick sweep.
        await self._sandbox_janitor_sweep()

        # Note: Per-agent settings are now generated at spawn time
        # via _generate_agent_settings() - no shared settings needed

        # A restart mid-execute orphans the release mutex in Redis (TTL 3000s,
        # no heartbeat after death); sweep stale keys so a CEO retry doesn't
        # hit already_in_progress for up to 50 min. Best-effort, inert if Redis
        # is down or empty.
        from roboco.services.release_proposal import sweep_orphan_release_locks

        await sweep_orphan_release_locks()

        # Obsidian vault: materialize the shipped .obsidian/ + _meta/ template
        # assets on first enable (idempotent — never overwrites an operator's
        # own edits). No-op when the flag is off.
        self._ensure_vault_assets_on_startup()

        # Start background tasks
        self._health_task = asyncio.create_task(self._health_loop())
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
        self._sweeper_task = asyncio.create_task(self._sweeper_loop())
        self._rate_limit_probe_task = asyncio.create_task(self._rate_limit_probe_loop())
        self._strategy_engine_task = asyncio.create_task(self._strategy_engine_loop())
        self._external_pr_poll_task = asyncio.create_task(self._external_pr_poll_loop())
        self._self_heal_task = asyncio.create_task(self._self_heal_loop())
        self._ci_watch_task = asyncio.create_task(self._ci_watch_loop())
        self._dep_update_task = asyncio.create_task(self._dep_update_loop())
        self._env_sync_task = asyncio.create_task(self._env_sync_loop())
        self._release_manager_task = asyncio.create_task(self._release_manager_loop())
        self._x_mentions_task = asyncio.create_task(self._x_mentions_poll_loop())
        self._board_program_task = asyncio.create_task(self._board_program_loop())
        self._video_render_task = asyncio.create_task(self._video_render_loop())
        self._vault_intake_task = asyncio.create_task(self._vault_intake_loop())
        self._vault_janitor_task = asyncio.create_task(self._vault_janitor_loop())
        self._vault_kb_task = asyncio.create_task(self._vault_kb_loop())
        self._telegram_poll_task = asyncio.create_task(self._telegram_poll_loop())

        logger.info(
            "Orchestrator started",
            dispatcher_interval=self.dispatcher_interval,
            internal_api_url=self._api_url,
        )

    async def _cancel_background_task(self, task: asyncio.Task | None) -> None:
        """Cancel one background loop task and await its teardown (idempotent)."""
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _drain_bg_tasks(self) -> None:
        """Let fire-and-forget ``_bg_tasks`` finish before the process exits.

        Short DB writes (a respawn_tracker upsert, an audit-log row) get a
        bounded window to commit — preserving the durable PM-respawn counter
        and the metrics-bearing audit trail — while a stuck task can't hang
        shutdown: past ``_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`` the still-pending
        tasks are cancelled. ``return_exceptions=True`` so one failing bg task
        doesn't crash the drain (a failed write already degraded to in-memory;
        logging it here would just be noise). No-op when nothing is pending.
        """
        pending = [t for t in self._bg_tasks if not t.done()]
        if not pending:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            for task in pending:
                if not task.done():
                    task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending, return_exceptions=True)

    async def _flush_respawn_tracker(self) -> None:
        """Persist the full in-memory PM-respawn snapshot before the process exits.

        Fire-and-forget persists (``_schedule_respawn_persist``) are bounded by
        the shutdown drain deadline; one cancelled by that deadline leaves the
        durable count lagging the in-memory counter, so the next restart
        re-burns the strike threshold against a still-wedged task — the exact
        re-burn the durable counter exists to stop (#74). Called from ``stop()``
        AFTER the bounded drain so it is the last writer (no further gate
        mutations fire once the agents and loops are down) and unbounded (a
        short upsert must not be dropped on the shutdown path). Best-effort: a
        row that fails to persist is logged and skipped, never crashing shutdown
        — the in-memory value is gone either way once the process exits.
        """
        if not self._pm_respawn_tracker:
            return
        for agent_slug, task_id in list(self._pm_respawn_tracker.keys()):
            record = self._pm_respawn_tracker.get((agent_slug, task_id))
            if record is None:
                continue
            try:
                await self._persist_respawn_record(agent_slug, task_id, dict(record))
            except Exception:
                logger.exception(
                    "shutdown respawn-tracker flush failed for one row; continuing",
                    agent_id=agent_slug,
                    task_id=task_id,
                )

    async def stop(self) -> None:
        """Stop the orchestrator and all agents."""
        if getattr(self, "_stopped", False):
            # Idempotent: the lifespan shutdown path stops the orchestrator
            # before closing the DB, and bootstrap's finally block re-calls
            # stop() as a safety net. The second call must be a no-op, not a
            # re-stop of already-stopped agents. ``getattr`` so a ``__new__``-
            # constructed instance (unit-test pattern) without ``__init__`` is
            # still stoppable.
            return
        self._running = False

        # Cancel every background loop, then stop the agents.
        for task in (
            self._health_task,
            self._dispatcher_task,
            self._sweeper_task,
            self._rate_limit_probe_task,
            self._strategy_engine_task,
            self._external_pr_poll_task,
            self._self_heal_task,
            self._ci_watch_task,
            self._dep_update_task,
            self._env_sync_task,
            self._release_manager_task,
            self._x_mentions_task,
            self._board_program_task,
            self._video_render_task,
            self._vault_intake_task,
            self._vault_janitor_task,
            self._vault_kb_task,
            self._telegram_poll_task,
        ):
            await self._cancel_background_task(task)

        # Stop all agents. One agent's stop error must not skip the drain
        # below — that would re-introduce the data-loss tail for every in-flight
        # bg write, so log-and-continue rather than propagate.
        for agent_id in list(self._instances.keys()):
            try:
                # release_claim=True: on shutdown the orchestrator is going
                # down and no agent will resume its task, so hand claimed
                # tasks back to the pool now — they re-dispatch immediately on
                # the next start instead of waiting for the reaper's TTL. A
                # provider-parked agent is skipped inside stop_agent so its
                # claim survives for the probe-resume loop across the restart.
                await self.stop_agent(
                    agent_id, release_claim=True, stop_reason="orchestrator_shutdown"
                )
            except Exception:
                logger.exception(
                    "stop_agent raised during shutdown; continuing to drain",
                    agent_id=agent_id,
                )

        # Drain fire-and-forget bg writes so short DB commits finish before the
        # process exits (respawn_tracker upserts, audit-log rows). Bounded so a
        # stuck task can't hang shutdown — it is cancelled past the deadline.
        await self._drain_bg_tasks()

        # #74: flush the authoritative in-memory respawn snapshot AFTER the
        # bounded drain so a deadline-cancelled persist can't leave the durable
        # count lagging the in-memory counter (and re-burning the strike
        # threshold on the next restart). Unbounded — a short upsert must not be
        # dropped on the shutdown path.
        await self._flush_respawn_tracker()

        self._stopped = True
        logger.info("Orchestrator stopped")

    # =========================================================================
    # PER-AGENT SETTINGS GENERATION
    # =========================================================================

    # =========================================================================
    # AGENT SPAWNING
    # =========================================================================

    _ROLES_WITH_AGENT_WORKSPACE: ClassVar[frozenset[str]] = frozenset(
        {"developer", "product_owner", "head_marketing"}
    )
    _ROLES_WITH_CELL_WORKSPACE: ClassVar[frozenset[str]] = frozenset({"documenter"})

    _TOOL_LOAD_CACHE: ClassVar[dict[str, str]] = {}

    # Per-role built-in tools, enumerated in the briefing so the agent
    # knows exactly what it has. These are pre-loaded at spawn via the
    # Claude Code `--tools` flag and gated only by the per-role
    # permission rules — NOT by ToolSearch (MCP-only; never gates
    # built-ins). Mirrors the system-prompt layer's _ROLE_BUILTIN_TOOLS
    # in roboco/agents/factories/_base.py — kept in sync because the
    # briefing and the system prompt are independent code paths.
    _COMMON_BUILTIN_TOOLS: ClassVar[tuple[str, ...]] = (
        "Read",
        "Bash",
        "Grep",
        "Glob",
        "TodoWrite",
    )
    _ROLE_BUILTIN_TOOLS: ClassVar[dict[str, tuple[str, ...]]] = {
        "developer": (*_COMMON_BUILTIN_TOOLS, "Edit", "Write"),
        "documenter": (*_COMMON_BUILTIN_TOOLS, "Edit", "Write"),
        "qa": _COMMON_BUILTIN_TOOLS,
        "main_pm": _COMMON_BUILTIN_TOOLS,
        "cell_pm": _COMMON_BUILTIN_TOOLS,
        "product_owner": _COMMON_BUILTIN_TOOLS,
        "head_marketing": _COMMON_BUILTIN_TOOLS,
        "auditor": _COMMON_BUILTIN_TOOLS,
        "pr_reviewer": _COMMON_BUILTIN_TOOLS,
    }

    # Slug -> team string for ROUTING purposes. Derived from
    # foundation.AGENTS so adding/renaming an agent edits exactly one
    # file (foundation/identity.py). The dispatcher relies on this for
    # task assignment routing categories.
    _AGENT_TEAM_MAP: ClassVar[dict[str, str]] = {
        slug: row.team.value for slug, row in _foundation.AGENTS.items()
    }

    _NOTIFICATION_COOLDOWN_PRUNE_AT = 512

    # =========================================================================
    # INTAKE (PROMPTER) LIVE SESSION
    #
    # The intake agent is not task-driven and is never dispatched. It is a
    # persistent Claude-Agent-SDK driver the CEO chats with live (the container
    # entrypoint is roboco.agent_sdk.intake_main). One fixed container —
    # `intake-1`, the seeded board-adjacent interviewer — serves one live
    # session at a time (single CEO; one-session-per-CEO).
    #
    # This spawn is a DELIBERATELY separate path from spawn_agent: no task, no
    # readiness gate, no `claude -p` CLI args (the image ENTRYPOINT is the
    # driver), no settings.json/hook mount (the driver owns the receiver on
    # port 9000, not the inbox sidecar), and no MCP/gateway surface (the live
    # agent reads code with Read/Grep/Glob and talks only to the human).
    # =========================================================================

    # ------------------------------------------------------------------ #
    # Secretary live session (mirrors intake; no scope clone; auth token)
    # ------------------------------------------------------------------ #

    # =========================================================================
    # AGENT STOPPING
    # =========================================================================

    # =========================================================================
    # WAITING STATE MANAGEMENT
    # =========================================================================

    # =========================================================================
    # PROVIDER QUERY HELPERS (used by the choreographer rate-limit path)
    # =========================================================================

    # =========================================================================
    # TOKEN USAGE INSTRUMENTATION
    # =========================================================================

    # =========================================================================
    # MEMBER-PERFORMANCE ROLLUP (granular per-member scorecards)
    # =========================================================================

    # =========================================================================
    # HEALTH MONITORING
    # =========================================================================

    # =========================================================================
    # RATE-LIMIT PROBE LOOP
    # =========================================================================

    # =========================================================================
    # STATUS API
    # =========================================================================

    def get_state(self, agent_id: str) -> AgentState:
        """Get current state of an agent."""
        if agent_id not in self._instances:
            return AgentState.OFFLINE
        return self._instances[agent_id].state

    def get_instance(self, agent_id: str) -> AgentInstance | None:
        """Get instance for an agent."""
        return self._instances.get(agent_id)

    def get_waiting_agents(self) -> dict[str, WaitingRecord]:
        """Get all waiting agents."""
        return dict(self._waiting_records)

    def get_status_summary(self) -> dict[str, Any]:
        """Get summary of all agent states."""
        by_state: dict[str, int] = {}
        agents: list[dict[str, Any]] = []

        for state in AgentState:
            count = sum(1 for i in self._instances.values() if i.state == state)
            if count > 0:
                by_state[state.value] = count

        for agent_id, instance in self._instances.items():
            cid = instance.container_id[:12] if instance.container_id else None
            agents.append(
                {
                    "agent_id": agent_id,
                    "state": instance.state.value,
                    "container_id": cid,
                    "task_id": instance.current_task_id,
                    "error_count": instance.error_count,
                    "started_at": instance.started_at.isoformat()
                    if instance.started_at
                    else None,
                }
            )

        return {
            "total": len(self._instances),
            "by_state": by_state,
            "waiting_count": len(self._waiting_records),
            "agents": agents,
        }

    # =========================================================================
    # SMART DISPATCHER - API HELPERS
    # =========================================================================

    # =========================================================================
    # SMART ROUTING - TASK CLASSIFICATION
    # =========================================================================

    # Keywords that indicate strategic/board-level tasks
    _BOARD_KEYWORDS = frozenset(
        {
            "roadmap",
            "architecture",
            "security",
            "budget",
            "hiring",
            "strategy",
            "vision",
            "milestone",
            "release",
            "launch",
        }
    )

    # Keywords that indicate PM coordination is needed
    _PM_KEYWORDS = frozenset(
        {
            "coordinate",
            "integration",
            "cross-team",
            "sync",
            "planning",
            "milestone",
            "dependencies",
            "review",
        }
    )

    # Keywords that indicate cross-cell work (requires Main PM)
    _CROSS_CELL_KEYWORDS = frozenset(
        {
            "all teams",
            "all cells",
            "every team",
            "every cell",
            "all departments",
            "cross-cell",
            "company-wide",
            "organization-wide",
            "backend and frontend",
            "frontend and backend",
            "all three",
        }
    )

    # Direct team-to-routing mappings (explicit assignments bypass keyword analysis)
    _TEAM_ROUTING_MAP: ClassVar[dict[str, str]] = {
        "main_pm": "main_pm",
        "board": "board",
        "marketing": "marketing",
    }

    # Team to PM mapping for routing
    _TEAM_PM_MAP: ClassVar[dict[str, str]] = {
        "backend": "be-pm",
        "frontend": "fe-pm",
        "ux_ui": "ux-pm",
    }

    # =========================================================================
    # SMART DISPATCHER - MAIN LOOP
    # =========================================================================

    def trigger_dispatch(self) -> None:
        """Wake the dispatcher up immediately for a single pass.

        Called by API routes right after a task status transition so the
        orchestrator reacts in milliseconds — e.g. a PM creates a subtask
        and the assignee spawns within a second instead of after the next
        30-second poll. Safe to call multiple times; the Event coalesces.
        """
        self._dispatch_wake.set()

    # Heartbeat cadence: one dispatcher.alive audit row per window. 300s keeps
    # audit_log growth trivial (~288 rows/day) while making a dead loop visible
    # within minutes (the 2026-07-01 outage was 4h25m of undetectable silence).
    _DISPATCH_HEARTBEAT_SECONDS = 300

    # How often _refresh_uptime actually re-queries audit_log, and how far
    # back it looks. Both loops (dispatcher + sweeper) call it every tick;
    # the throttle keeps that to one query per minute regardless.
    UPTIME_REFRESH_SECONDS = 60
    UPTIME_LOOKBACK_DAYS = 7

    # =========================================================================
    # SMART DISPATCHER - TASK-BASED DISPATCHERS
    # =========================================================================

    _PM_AGENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "main-pm",
            "be-pm",
            "fe-pm",
            "ux-pm",
        }
    )

    # Board reviewers. They advise — review + record requirements + escalate —
    # but do not build or delegate. Dispatched once per assigned board task.
    _BOARD_AGENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "product-owner",
            "head-marketing",
        }
    )

    # Use foundation's default; keep the local name for back-compat.
    _PM_RESPAWN_MAX_UNPRODUCTIVE = _AGENT_LOOP_BUDGET.pm_respawn_max_unproductive
    _PM_RESPAWN_MAX_TRACING_RESETS = _AGENT_LOOP_BUDGET.pm_respawn_max_tracing_resets
    _PM_RESPAWN_MAX_REVISIT_RESETS = _AGENT_LOOP_BUDGET.pm_respawn_max_revisit_resets
    _PM_RESPAWN_TRIP_COOLDOWN_SECONDS = (
        _AGENT_LOOP_BUDGET.pm_respawn_trip_cooldown_seconds
    )

    # Which flow route + verb submits an assembled parent, per PM role.
    _AUTO_SUBMIT_VERB_BY_ROLE: ClassVar[dict[str, tuple[str, str]]] = {
        "cell_pm": ("cell_pm", "submit_up"),
        "main_pm": ("main_pm", "submit_root"),
    }

    # Bounds get_pr_ci_status calls to roughly one per PR per window instead
    # of one per dispatch tick per task - the dispatch loop polls every
    # `dispatcher_interval` (30s default) and can wake far more often on a
    # busy fleet (trigger_dispatch fires on every status transition).
    _GATE_CI_STATUS_CACHE_TTL_SECONDS: ClassVar[float] = 60.0

    # =========================================================================
    # SMART DISPATCHER - EVENT-BASED DISPATCHERS
    # =========================================================================

    _MIN_DESCRIPTION_LEN = 10

    # =========================================================================
    # SMART DISPATCHER - PROMPT BUILDERS
    # =========================================================================


# A handful of the moved-verbatim method bodies above call class-qualified
# static/classmethod-style helpers by bare name (`AgentOrchestrator._foo(...)`
# rather than `self._foo(...)`; see analyze.py's SELF_LIKE_RECEIVERS comment)
# from mixins that are NOT the module defining AgentOrchestrator. A real
# top-level `from roboco.runtime.orchestrator import AgentOrchestrator` in
# those mixins would be a circular import (this module imports them before
# its own AgentOrchestrator class exists); each of them keeps the name
# TYPE_CHECKING-only for static analysis instead (see their own docstring
# note) and gets the live class object bound into its globals here, once
# AgentOrchestrator is fully defined -- so the bare-name references resolve
# correctly the first time any such method actually runs.
for _mod_name in (
    "interactive_sessions",
    "spawn_config",
    "spawn_exit",
    "spawn_launch",
):
    setattr(
        sys.modules[f"roboco.runtime.engines.{_mod_name}"],
        "AgentOrchestrator",
        AgentOrchestrator,
    )
del _mod_name
