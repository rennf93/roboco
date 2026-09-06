"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: spawn_exit)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import status as http_status

from roboco.config import settings
from roboco.models.runtime import (
    AgentInstance,
    WaitingRecord,
)
from roboco.runtime.orchestrator import (
    _ANTHROPIC_AUTH_MARKERS,
    _ANTHROPIC_AUTH_RETRY_AFTER_S,
    _CODEX_AUTH_EXIT_CODE,
    _CODEX_AUTH_RETRY_AFTER_S,
    _CODEX_RATE_LIMIT_EXIT_CODE,
    _CODEX_RATE_LIMIT_RETRY_AFTER_S,
    _DOCKER_INSPECT_TIMEOUT_SECONDS,
    _EXPECTED_STOP_FRESH_SECONDS,
    _EXPECTED_STOP_MAX_ENTRIES,
    _GEMINI_AUTH_EXIT_CODE,
    _GEMINI_RATE_LIMIT_EXIT_CODE,
    _GEMINI_REPARK_BACKOFF_CAP,
    _GEMINI_REPARK_EPISODE_GAP_S,
    _GROK_AUTH_EXIT_CODE,
    _GROK_AUTH_RETRY_AFTER_S,
    _GROK_RATE_LIMIT_EXIT_CODE,
    _GROK_RATE_LIMIT_RETRY_AFTER_S,
    _GROK_REPARK_BACKOFF_CAP,
    _GROK_REPARK_EPISODE_GAP_S,
    _KIMI_AUTH_EXIT_CODE,
    _KIMI_RATE_LIMIT_EXIT_CODE,
    _OVERLOAD_MARKERS_BY_PROVIDER,
    _OVERLOAD_RETRY_AFTER_S,
    _RATE_LIMIT_MARKERS_BY_PROVIDER,
    _RATE_LIMIT_RETRY_AFTER_S,
    CODEX_USAGE_DATA_DIR,
    GEMINI_USAGE_DATA_DIR,
    GROK_USAGE_DATA_DIR,
    KIMI_USAGE_DATA_DIR,
    PROJECT_HOST_PATH,
    SDK_PORT,
    AgentState,
    _system_api_headers,
    logger,
)

if TYPE_CHECKING:
    # Also bound into this module's real (non-TYPE_CHECKING) globals at
    # runtime by orchestrator.py, right after the class is defined -- see
    # its bottom-of-file comment.
    from roboco.runtime.orchestrator import (
        AgentOrchestrator,
    )


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class SpawnExitEngine(_Base):
    """Mixin holding the "spawn_exit" methods moved out of AgentOrchestrator."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _park_grok_rate_limited and
    # _park_gemini_rate_limited below) without a bare re-declaration
    # directly on the concrete class.
    if TYPE_CHECKING:
        _grok_last_park_at: datetime | None
        _grok_repark_count: int
        _gemini_last_park_at: datetime | None
        _gemini_repark_count: int

    @staticmethod
    def _grok_usage_root() -> Path:
        """The base dir all per-agent grok usage dirs live under (no agent id).

        Branched compose-vs-local: in compose the orchestrator sees the mounted
        host dir at ``GROK_USAGE_DATA_DIR``; in local mode usage.json lands under
        the shared tempdir. The single fixed anchor the per-agent dir hangs off,
        and the safe root a finalize read is checked to stay within.
        """
        if PROJECT_HOST_PATH:
            return Path(GROK_USAGE_DATA_DIR)
        return Path(tempfile.gettempdir()) / "roboco-grok-usage"

    @staticmethod
    def _codex_usage_root() -> Path:
        """The base dir all per-agent codex usage dirs live under (no agent id).

        Same compose-vs-local branch as :meth:`_grok_usage_root`.
        """
        if PROJECT_HOST_PATH:
            return Path(CODEX_USAGE_DATA_DIR)
        return Path(tempfile.gettempdir()) / "roboco-codex-usage"

    @staticmethod
    def _gemini_usage_root() -> Path:
        """The base dir all per-agent gemini usage dirs live under (no agent id).

        Branched compose-vs-local exactly like :meth:`_grok_usage_root`.
        """
        if PROJECT_HOST_PATH:
            return Path(GEMINI_USAGE_DATA_DIR)
        return Path(tempfile.gettempdir()) / "roboco-gemini-usage"

    @staticmethod
    def _kimi_usage_root() -> Path:
        """The base dir all per-agent kimi usage dirs live under (no agent id).

        Same compose-vs-local branch as :meth:`_grok_usage_root`.
        """
        if PROJECT_HOST_PATH:
            return Path(KIMI_USAGE_DATA_DIR)
        return Path(tempfile.gettempdir()) / "roboco-kimi-usage"

    def _record_expected_stop(self, agent_id: str, reason: str) -> None:
        """Breadcrumb an orchestrator-initiated stop/kill for ``agent_id``.

        Diagnostics only (in-memory, no DB): lets the exit monitor tell an
        attributed stop from a genuinely unexplained one instead of logging
        every death as "unexpectedly". Bounded: past a size threshold, stale
        entries are dropped opportunistically rather than growing forever.
        ``getattr`` defaults the registry so a ``__new__``-constructed test
        instance (no ``__init__``) doesn't need to know about it either.
        """
        stops: dict[str, tuple[str, float]] | None = getattr(
            self, "_expected_stops", None
        )
        if stops is None:
            stops = self._expected_stops = {}
        if len(stops) > _EXPECTED_STOP_MAX_ENTRIES:
            cutoff = time.monotonic() - _EXPECTED_STOP_FRESH_SECONDS
            stops = self._expected_stops = {
                k: v for k, v in stops.items() if v[1] >= cutoff
            }
        stops[agent_id] = (reason, time.monotonic())

    def _consume_expected_stop(self, agent_id: str) -> str:
        """Pop and return the breadcrumb reason for ``agent_id``, else "none_recorded".

        A breadcrumb older than ``_EXPECTED_STOP_FRESH_SECONDS`` is treated as
        stale (not fresh enough to attribute to *this* exit) and reported the
        same as no breadcrumb at all. Defensive on a missing registry, like
        ``_record_expected_stop``.
        """
        stops: dict[str, tuple[str, float]] | None = getattr(
            self, "_expected_stops", None
        )
        entry = stops.pop(agent_id, None) if stops else None
        if entry is None:
            return "none_recorded"
        reason, recorded_at = entry
        if time.monotonic() - recorded_at > _EXPECTED_STOP_FRESH_SECONDS:
            return "none_recorded"
        return reason

    @staticmethod
    async def _inspect_exit_diagnostics(container_name: str) -> dict[str, Any]:
        """Best-effort extra `docker inspect` fields for a dead container's log line.

        Cheap (one more inspect the monitor already does one of) and never
        raises — any failure/timeout yields {} so the caller's log line still
        emits with whatever fields it already had.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "-f",
                "{{.State.OOMKilled}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|"
                "{{.State.Error}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_DOCKER_INSPECT_TIMEOUT_SECONDS
            )
            oom, started, finished, error = stdout.decode().strip().split("|")
        except Exception:
            return {}
        return {
            "oom_killed": oom == "true",
            "started_at": started or None,
            "finished_at": finished or None,
            "state_error": error or None,
        }

    async def _remove_container(
        self,
        container_name: str,
        *,
        teardown_sandbox: bool = True,
        stop_reason: str | None = None,
    ) -> None:
        """Remove a container if it exists, dumping its logs to disk first.

        Docker deletes the container's json-file log when we `docker rm`, so
        before removal we copy the current log to /data/logs/agents/{slug}/
        with a timestamp. That gives us persistent history across respawns
        without needing an entrypoint wrapper inside the agent image.

        ``teardown_sandbox=False`` is passed only by the pre-spawn stale-clear,
        whose spawn has already provisioned the sandbox it is about to use.

        ``stop_reason``, when given, breadcrumbs this removal so the exit
        monitor can attribute the death instead of flagging it unexplained.
        ``None`` (the default) skips it — used by callers (``stop_agent``)
        that already recorded their own breadcrumb earlier, before their
        docker stop/kill, so this call doesn't clobber it with "unknown".
        """
        if stop_reason is not None:
            self._record_expected_stop(
                container_name.removeprefix("roboco-agent-"), stop_reason
            )
        # Check the container actually exists before trying to dump logs;
        # _remove_container is routinely called pre-spawn to clear stale
        # containers, and on first spawn there's nothing to dump.
        inspect = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format={{.Id}}",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        exists = (await inspect.wait()) == 0

        if exists:
            slug = container_name.removeprefix("roboco-agent-")
            try:
                # slug builds a path under /data/logs/agents — reject a
                # traversal-shaped slug before the join (defense-in-depth;
                # spawn_agent already validates the agent_id this container
                # name is derived from). A bad slug skips the log dump.
                AgentOrchestrator._safe_agent_path_segment(slug)
                log_dir = Path("/data/logs/agents") / slug
                log_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                log_path = log_dir / f"{timestamp}.log"
                with log_path.open("wb") as out:
                    dump_proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "logs",
                        container_name,
                        stdout=out,
                        stderr=out,
                    )
                    await dump_proc.wait()
                if log_path.stat().st_size == 0:
                    log_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(
                    "Could not dump container logs before removal",
                    container=container_name,
                    error=str(e),
                )

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        # Sandbox lifetime tracks the agent container 1:1 — every removal
        # path (stop_agent, reaper kills) routes through here. Gated on the
        # flag: teardown is idempotent but not free (up to 4 extra docker
        # calls), so skip it when the feature was never on. Never raises
        # (SandboxProvisioner.teardown contract).
        if teardown_sandbox and settings.sandbox_db_enabled:
            slug = container_name.removeprefix("roboco-agent-")
            await self._sandbox.teardown(slug)
            # Evict the ensure_sandbox cache so a later request_sandbox call
            # re-provisions instead of handing back creds for a torn-down
            # container. getattr guards bare __new__() test doubles that
            # never ran __init__.
            cache = getattr(self, "_sandbox_info", None)
            if cache is not None:
                cache.pop(slug, None)

    async def mark_waiting_long(
        self,
        agent_id: str,
        waiting_for: str,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark an agent as WAITING_LONG and terminate.

        The agent will be respawned when the wait condition is resolved.
        The record is mirrored to `waiting_records` in Postgres so a later
        orchestrator restart can still resolve the wait.
        """
        record = WaitingRecord(
            agent_id=agent_id,
            task_id=task_id,
            waiting_for=waiting_for,
            waiting_since=datetime.now(UTC),
            context=context or {},
        )

        self._waiting_records[agent_id] = record
        await self._persist_waiting_record(record)

        # Stop the agent
        await self.stop_agent(agent_id, stop_reason=f"waiting_long_{waiting_for}")

        # Update state
        if agent_id in self._instances:
            self._instances[agent_id].state = AgentState.WAITING_LONG
            self._instances[agent_id].waiting_for = waiting_for
            self._instances[agent_id].waiting_context = context or {}

        logger.info(
            "Agent marked as waiting_long",
            agent_id=agent_id,
            waiting_for=waiting_for,
            task_id=task_id,
        )

    async def _persist_waiting_record(self, record: WaitingRecord) -> None:
        """Upsert a WaitingRecord into the waiting_records table."""
        try:
            from uuid import UUID as _UUID

            from sqlalchemy import delete

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                # One record per agent; delete prior then insert.
                await db.execute(
                    delete(WaitingRecordTable).where(
                        WaitingRecordTable.agent_id == record.agent_id
                    )
                )
                row = WaitingRecordTable(
                    agent_id=record.agent_id,
                    task_id=(_UUID(record.task_id) if record.task_id else None),
                    waiting_for=record.waiting_for,
                    waiting_since=record.waiting_since,
                    context=record.context,
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to persist waiting record",
                agent_id=record.agent_id,
                error=str(e),
            )

    def get_provider_for_agent(self, agent_slug: str) -> str | None:
        """Return the ``provider_type`` for a currently-tracked agent, or None.

        Reads the in-memory ``_instances`` dict so this is synchronous and
        O(1).  Returns None when the agent is not tracked or has no config.

        Args:
            agent_slug: The agent slug (e.g. ``"be-dev-1"``).
        """
        instance = self._instances.get(agent_slug)
        if instance is None or instance.config is None:
            return None
        return instance.config.provider_type

    def get_active_agent_slugs_for_provider(self, provider: str) -> list[str]:
        """Return slugs of all active agents currently using ``provider``.

        "Active" means the instance's state is ACTIVE or STARTING (i.e.
        the container is running or spinning up — not IDLE, WAITING_LONG,
        STOPPING, or OFFLINE).

        Args:
            provider: Provider type string, e.g. ``"anthropic"`` or
                      ``"ollama_cloud"``.
        """
        active_states = {AgentState.ACTIVE, AgentState.STARTING}
        return [
            slug
            for slug, inst in self._instances.items()
            if inst.state in active_states
            and inst.config is not None
            and inst.config.provider_type == provider
        ]

    def _claude_session_id_for(self, agent_id: str) -> str | None:
        """The orchestrator-assigned Claude session id for a running agent."""
        instance = self._instances.get(agent_id)
        return (
            instance.config.claude_session_id if instance and instance.config else None
        )

    @staticmethod
    def _usage_from_transcript(
        agent_id: str, claude_session_id: str | None = None
    ) -> tuple[int, int, int, int, int]:
        """Sum token usage + turn count from the agent's Claude Code transcript.

        The host ``~/.claude`` is mounted into the orchestrator, so transcripts
        are readable here under ``projects/<cwd-dir>/<session-id>.jsonl``. When
        the orchestrator-assigned ``claude_session_id`` is known we locate the
        exact transcript by id across ANY project dir — review/coordinate roles
        run at cwd ``/app`` so theirs lands in ``projects/-app``, not in a
        per-agent ``projects/*-{slug}`` dir. Without an id we fall back to the
        newest transcript in the agent's own workspace dir. Durable fallback for
        the live SDK ``/usage/status`` fetch, which misses for short-lived or
        torn-down agents. Returns zeros when no transcript is found.
        """
        from roboco.agent_sdk.transcript_usage import sum_transcript_usage

        projects = Path.home() / ".claude" / "projects"
        try:
            if claude_session_id:
                by_id = list(projects.glob(f"*/{claude_session_id}.jsonl"))
                if by_id:
                    return sum_transcript_usage(by_id[0])
            jsonl = [
                f
                for d in projects.glob(f"*-{agent_id}")
                if d.is_dir()
                for f in d.glob("*.jsonl")
            ]
            if not jsonl:
                return (0, 0, 0, 0, 0)
            newest = max(jsonl, key=lambda f: f.stat().st_mtime)
            return sum_transcript_usage(newest)
        except OSError:
            return (0, 0, 0, 0, 0)

    def _grok_usage_json(self, agent_id: str) -> dict[str, Any] | None:
        """Read a GROK agent's ``usage.json`` (``{model, total_tokens, cost_usd}``).

        Written to the per-agent data dir by the grok-CLI entrypoint (one-shot,
        post-run) and the interactive driver (per-turn); read back from the same
        branched dir the writers mount (``_grok_usage_dir``). Returns ``None`` when
        absent / unreadable.
        """
        return self._read_usage_json_contained(self._grok_usage_root(), agent_id)

    @staticmethod
    def _read_usage_json_contained(base: Path, agent_id: str) -> dict[str, Any] | None:
        """Read ``<base>/<agent_id>/usage.json`` behind a containment barrier.

        Agent ids are orchestrator-assigned slugs/uuids and the dir builders
        already validate them (``_safe_agent_path_segment``), but the read
        applies the resolve-and-contain check anyway: the id is reduced to its
        final path component, the full path resolved, and any result outside
        the resolved usage root refused — a hostile id can never escape the
        root regardless of upstream drift. Returns ``None`` when refused,
        absent, or unreadable.
        """
        # Barrier (CWE-022): the id must be a single allowlisted token — the
        # orchestrator only ever assigns slug/uuid ids ([A-Za-z0-9._-]), none
        # of which can contain a path separator or ``..`` traversal (the
        # required alphanumeric first char already rejects ``.``/``..``). This
        # standalone regexp fullmatch is the primary sanitizer; the
        # realpath+startswith containment below is defense-in-depth.
        segment = os.path.basename(agent_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", segment):
            return None
        try:
            root = os.path.realpath(base)
            candidate = os.path.realpath(base / segment / "usage.json")
            if candidate != root and not candidate.startswith(root + os.sep):
                return None
            with Path(candidate).open(encoding="utf-8") as handle:
                data = json.loads(handle.read())
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _grok_usage_tokens(self, agent_id: str) -> tuple[int, int, int, int]:
        """A GROK agent's token usage from its ``usage.json``.

        grok reports a single cumulative total with no input/output split, so it
        folds into output (it bills at the output rate, matching
        ``calculate_cost``). A WARNING is logged on a missing/zero read because a
        silent mount/uid failure is otherwise indistinguishable from a genuine
        zero-cost run. Returns ``(input, output, cache_read, cache_write)``.
        """
        data = self._grok_usage_json(agent_id)
        total = 0
        if data:
            try:
                total = int(data.get("total_tokens", 0))
            except (TypeError, ValueError):
                total = 0
        if not total:
            logger.warning(
                "GROK agent finalized with no readable usage "
                "(0 tokens / $0) — check the data dir mount",
                agent_id=agent_id,
            )
        return (0, total, 0, 0)

    def _codex_usage_json(self, agent_id: str) -> dict[str, Any] | None:
        """Read an OPENAI agent's ``usage.json`` (mirrors ``_grok_usage_json``).

        Written by the codex-cli entrypoint (one-shot, post-run) to the
        per-agent dir under ``_codex_usage_dir``. Returns ``None`` when
        absent / unreadable.
        """
        return self._read_usage_json_contained(self._codex_usage_root(), agent_id)

    def _gemini_usage_json(self, agent_id: str) -> dict[str, Any] | None:
        """Read a GEMINI agent's ``usage.json`` (``{model, total_tokens, cost_usd}``).

        Written to the per-agent data dir by the gemini-cli entrypoint
        (one-shot, post-run); read back from the same branched dir the writer
        mounts (``_gemini_usage_dir``). Returns ``None`` when absent/unreadable
        — shares ``_read_usage_json_contained``'s resolve-and-contain barrier
        with the grok/codex reads.
        """
        return self._read_usage_json_contained(self._gemini_usage_root(), agent_id)

    def _kimi_usage_json(self, agent_id: str) -> dict[str, Any] | None:
        """Read a KIMI agent's ``usage.json`` (mirrors ``_codex_usage_json``).

        Written by the kimi-cli entrypoint (one-shot, post-run) to the
        per-agent dir under ``_kimi_usage_dir``. Returns ``None`` when
        absent / unreadable.
        """
        return self._read_usage_json_contained(self._kimi_usage_root(), agent_id)

    def _codex_usage_tokens(self, agent_id: str) -> tuple[int, int, int, int]:
        """An OPENAI agent's token usage from its ``usage.json``.

        Unlike grok's single cumulative total, codex reports a real
        input/output/cache split (see ``codex_cli_usage``), so this returns
        the genuine 4-tuple instead of folding everything into output. A
        WARNING logs on a missing/zero read (a silent mount/uid failure is
        otherwise indistinguishable from a genuine zero-cost run).
        """
        data = self._codex_usage_json(agent_id)
        tokens = (0, 0, 0, 0)
        if data:
            try:
                tokens = (
                    int(data.get("tokens_input", 0)),
                    int(data.get("tokens_output", 0)),
                    int(data.get("tokens_cache_read", 0)),
                    int(data.get("tokens_cache_write", 0)),
                )
            except (TypeError, ValueError):
                tokens = (0, 0, 0, 0)
        if not tokens[0] and not tokens[1]:
            logger.warning(
                "OPENAI (codex) agent finalized with no readable usage "
                "(0 tokens / $0) — check the data dir mount",
                agent_id=agent_id,
            )
        return tokens

    def _codex_usage_turns(self, agent_id: str) -> int:
        """An OPENAI agent's turn count from its ``usage.json`` (0 if none).

        Codex's JSONL carries a real ``turn.completed`` count (unlike grok,
        which has no turn signal at all) — see ``codex_cli_usage``.
        """
        data = self._codex_usage_json(agent_id)
        if not data:
            return 0
        try:
            return int(data.get("turns", 0))
        except (TypeError, ValueError):
            return 0

    def _kimi_usage_tokens(self, agent_id: str) -> tuple[int, int, int, int]:
        """A KIMI agent's token usage from its ``usage.json``.

        Kimi's ``wire.jsonl`` carries a real, already-disjoint 4-bucket split
        (see ``kimi_cli_usage``), so this returns the genuine tuple instead of
        folding everything into output (parity with ``_codex_usage_tokens``).
        A WARNING logs on a missing/zero read (a silent mount/uid failure is
        otherwise indistinguishable from a genuine zero-cost run).
        """
        data = self._kimi_usage_json(agent_id)
        tokens = (0, 0, 0, 0)
        if data:
            try:
                tokens = (
                    int(data.get("tokens_input", 0)),
                    int(data.get("tokens_output", 0)),
                    int(data.get("tokens_cache_read", 0)),
                    int(data.get("tokens_cache_write", 0)),
                )
            except (TypeError, ValueError):
                tokens = (0, 0, 0, 0)
        if not tokens[0] and not tokens[1]:
            logger.warning(
                "KIMI agent finalized with no readable usage "
                "(0 tokens / $0) — check the sessions dir mount",
                agent_id=agent_id,
            )
        return tokens

    def _kimi_usage_turns(self, agent_id: str) -> int:
        """A KIMI agent's turn count from its ``usage.json`` (0 if none).

        Kimi's wire.jsonl carries a real per-turn ``usage.record`` count
        (see ``kimi_cli_usage``), the same parity ``_codex_usage_turns`` has
        over grok's turn-less usage.json.
        """
        data = self._kimi_usage_json(agent_id)
        if not data:
            return 0
        try:
            return int(data.get("turns", 0))
        except (TypeError, ValueError):
            return 0

    def _gemini_usage_tokens(self, agent_id: str) -> tuple[int, int, int, int]:
        """A GEMINI agent's token usage from its ``usage.json``.

        The entrypoint's usage capture already priced each model's real
        input/output split at its OWN rate (unlike grok's single-model
        blanket), but flattens to one ``total_tokens`` for this shape — so,
        like grok, the whole total folds into output here. A WARNING is
        logged on a missing/zero read (a silent mount/uid failure is otherwise
        indistinguishable from a genuine zero-cost run).
        """
        data = self._gemini_usage_json(agent_id)
        total = 0
        if data:
            try:
                total = int(data.get("total_tokens", 0))
            except (TypeError, ValueError):
                total = 0
        if not total:
            logger.warning(
                "GEMINI agent finalized with no readable usage "
                "(0 tokens / $0) — check the data dir mount",
                agent_id=agent_id,
            )
        return (0, total, 0, 0)

    async def _resolve_final_token_usage(
        self, agent_id: str
    ) -> tuple[int, int, int, int]:
        """Resolve final token counts for a stopping agent.

        For a GROK, OPENAI (codex), GEMINI, or KIMI agent, reads the captured
        ``usage.json`` (no SDK server / Claude transcript exists for any of
        them). Otherwise tries the live SDK ``/usage/status`` first; if that
        misses — the SDK's in-memory counts race container teardown for
        short-lived agents — it falls back to the agent's Claude Code
        transcript, which is durable and mounted into this container. Returns
        ``(input, output, cache_read, cache_write)``.
        """
        from roboco.models.base import ModelProvider

        provider = self.get_provider_for_agent(agent_id)
        # One-shot CLIs (no SDK server / Claude transcript) each read their own
        # captured usage.json — collapsed into a lookup (mirrors
        # _resolve_active_tokens's usage_json_readers) so a fourth such
        # provider is one dict entry, not another branch (keeps this
        # function's xenon budget flat).
        usage_json_readers = {
            ModelProvider.GROK.value: self._grok_usage_tokens,
            ModelProvider.OPENAI.value: self._codex_usage_tokens,
            ModelProvider.GEMINI.value: self._gemini_usage_tokens,
            ModelProvider.KIMI.value: self._kimi_usage_tokens,
        }
        read_usage_json = usage_json_readers.get(provider) if provider else None
        if read_usage_json is not None:
            return read_usage_json(agent_id)
        return await self._resolve_final_token_usage_from_sdk(agent_id)

    async def _resolve_final_token_usage_from_sdk(
        self, agent_id: str
    ) -> tuple[int, int, int, int]:
        """SDK ``/usage/status`` + Claude-transcript fallback for a Claude-path
        agent — split out of ``_resolve_final_token_usage`` to keep its own
        xenon budget flat as one-shot-CLI providers accrete."""
        tokens = (0, 0, 0, 0)
        sdk_url = f"http://roboco-agent-{agent_id}:{SDK_PORT}/usage/status"
        try:
            async with httpx.AsyncClient(
                timeout=3.0, headers=_system_api_headers()
            ) as client:
                resp = await client.get(sdk_url)
                if resp.status_code == http_status.HTTP_200_OK:
                    data = resp.json()
                    tokens = (
                        data.get("tokens_input", 0),
                        data.get("tokens_output", 0),
                        data.get("tokens_cache_read", 0),
                        data.get("tokens_cache_write", 0),
                    )
        except Exception as sdk_exc:
            logger.debug(
                "Could not fetch final token counts from SDK",
                agent_id=agent_id,
                error=str(sdk_exc),
            )

        if not tokens[0] and not tokens[1]:
            tin, tout, cr, cw, _turns = self._usage_from_transcript(
                agent_id, self._claude_session_id_for(agent_id)
            )
            if tin or tout:
                tokens = (tin, tout, cr, cw)
        return tokens

    async def _resolve_final_turns_tools(self, agent_id: str) -> tuple[int, int]:
        """Resolve final ``(turns, tool_calls)`` for a stopping agent.

        Primary source is the live SDK ``/usage/status`` (which carries both).
        For ``turns`` only there is a durable Claude-transcript fallback (unique
        assistant-message count) for short-lived agents whose SDK counts race
        teardown; ``tool_calls`` has no transcript equivalent and stays 0 ("n/a")
        when the SDK misses. Grok and Gemini agents have neither — returns
        ``(0, 0)``. Codex and Kimi agents each have a real per-turn count
        (from their own usage.json) but no tool-call signal — returns
        ``(turns, 0)``. Best-effort: any failure degrades to zeros, never
        blocks finalize.
        """
        from roboco.models.base import ModelProvider

        provider = self.get_provider_for_agent(agent_id)
        if provider in (ModelProvider.GROK.value, ModelProvider.GEMINI.value):
            return (0, 0)
        if provider == ModelProvider.OPENAI.value:
            return (self._codex_usage_turns(agent_id), 0)
        if provider == ModelProvider.KIMI.value:
            return (self._kimi_usage_turns(agent_id), 0)

        turns = tool_calls = 0
        sdk_url = f"http://roboco-agent-{agent_id}:{SDK_PORT}/usage/status"
        try:
            async with httpx.AsyncClient(
                timeout=3.0, headers=_system_api_headers()
            ) as client:
                resp = await client.get(sdk_url)
                if resp.status_code == http_status.HTTP_200_OK:
                    data = resp.json()
                    turns = int(data.get("turns", 0) or 0)
                    tool_calls = int(data.get("tool_calls", 0) or 0)
        except Exception as sdk_exc:
            logger.debug(
                "Could not fetch final turns/tool_calls from SDK",
                agent_id=agent_id,
                error=str(sdk_exc),
            )

        if not turns:
            *_tokens, t = self._usage_from_transcript(
                agent_id, self._claude_session_id_for(agent_id)
            )
            turns = t
        return turns, tool_calls

    async def _finalize_spawn_session(
        self,
        agent_id: str,
        exit_reason: str = "stopped",
    ) -> None:
        """Close the open agent_spawn_sessions row for this agent.

        Resolves final token counts (live SDK, with a durable transcript
        fallback), calculates cost via the pricing module, then updates the DB
        row with ended_at, token totals, exit_reason, and estimated_cost_usd.
        Errors are caught and logged — finalization must never block stop_agent.
        """
        try:
            from roboco.billing.pricing import calculate_cost
            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable

            # Resolve final token counts (live SDK, with transcript fallback).
            (
                tokens_input,
                tokens_output,
                tokens_cache_read,
                tokens_cache_write,
            ) = await self._resolve_final_token_usage(agent_id)
            # Resolve LLM iterations + tool calls (live SDK; turns has a
            # transcript fallback). Separate from the token tuple so the live
            # snapshot helpers keep their 4-tuple contract.
            turns, tool_calls = await self._resolve_final_turns_tools(agent_id)

            # Look up the model and usage_session_id from the running instance config.
            model = "unknown"
            instance = self._instances.get(agent_id)
            if instance and instance.config:
                model = instance.config.model or "unknown"
            usage_session_id = instance.usage_session_id if instance else None
            doctrine_version = self._doctrine_version_for_instance(instance)

            cost = calculate_cost(
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_cache_read=tokens_cache_read,
                tokens_cache_write=tokens_cache_write,
            )

            session_factory = get_session_factory()
            async with session_factory() as db:
                from sqlalchemy import select, update

                # Prefer a direct lookup by the session UUID captured at spawn
                # time; fall back to the (agent_slug, ended_at IS NULL) query
                # for instances that pre-date the usage_session_id field.
                if usage_session_id is not None:
                    result = await db.execute(
                        select(AgentSpawnSessionTable).where(
                            AgentSpawnSessionTable.id == usage_session_id
                        )
                    )
                else:
                    result = await db.execute(
                        select(AgentSpawnSessionTable)
                        .where(
                            AgentSpawnSessionTable.agent_slug == agent_id,
                            AgentSpawnSessionTable.ended_at.is_(None),
                        )
                        .order_by(AgentSpawnSessionTable.started_at.desc())
                        .limit(1)
                    )
                session_row = result.scalar_one_or_none()
                if session_row is not None:
                    await db.execute(
                        update(AgentSpawnSessionTable)
                        .where(AgentSpawnSessionTable.id == session_row.id)
                        .values(
                            ended_at=datetime.now(UTC),
                            tokens_input=tokens_input,
                            tokens_output=tokens_output,
                            tokens_cache_read=tokens_cache_read,
                            tokens_cache_write=tokens_cache_write,
                            turns=turns,
                            tool_calls=tool_calls,
                            exit_reason=exit_reason,
                            estimated_cost_usd=cost,
                            doctrine_version=doctrine_version,
                        )
                    )
                    await db.commit()
                    logger.debug(
                        "Spawn session finalized",
                        agent_id=agent_id,
                        session_id=str(session_row.id),
                        tokens_input=tokens_input,
                        tokens_output=tokens_output,
                        estimated_cost_usd=cost,
                        doctrine_version=doctrine_version,
                    )
        except Exception as exc:
            logger.warning(
                "Failed to finalize spawn session",
                agent_id=agent_id,
                error=str(exc),
            )

    @staticmethod
    def _doctrine_version_for_instance(instance: AgentInstance | None) -> str | None:
        """Short hash of the composed system prompt this spawn ran with.

        Reads the SAME file ``_generate_composed_prompt`` wrote at spawn
        preparation (``config.blueprint_path``) — nothing on the spawn/stop
        path deletes it, so it is still the exact prompt text this agent ran
        with. Every provider gets one (the blueprint is composed and written
        unconditionally in ``_prepare_agent_spawn``, before provider/route
        resolution) — GROK agents carry a real blueprint file too, same as
        Claude. Best-effort: a missing/unreadable file (a provider-parked stub
        instance that never actually spawned — ``blueprint_path=Path()`` — an
        evicted temp dir, ...) is NULL, never a finalize failure — the eval
        harness treats an unstamped session as "doctrine unknown", not an
        error.
        """
        if instance is None or instance.config is None:
            return None
        blueprint_path = instance.config.blueprint_path
        if not blueprint_path:
            return None
        try:
            content = blueprint_path.read_text()
        except OSError:
            return None
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    async def _inspect_container_state(
        container_name: str,
    ) -> tuple[bool, int | None]:
        """Return (is_running, exit_code) from `docker inspect`.

        exit_code is None when the output is missing or unparseable; the
        caller treats None as a crash for safety.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}} {{.State.ExitCode}}",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_DOCKER_INSPECT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            proc.kill()
            raise
        parts = stdout.decode().strip().split()
        is_running = bool(parts) and parts[0] == "true"
        try:
            exit_code = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            exit_code = None
        return is_running, exit_code

    async def _maybe_park_for_exit_error(
        self, agent_id: str, instance: Any, graceful: bool
    ) -> bool:
        """Park the provider on a session/usage limit or a server overload detected
        in the dead container's output, instead of crash-retrying into it. Returns
        True when parked (caller returns); False to proceed with normal handling.
        The probe-resume loop revives the task when the limit lifts / overload clears.
        """
        if graceful:
            return False
        rate_limited_provider = await self._provider_rate_limit_park_target(
            agent_id, instance
        )
        if rate_limited_provider is not None:
            logger.warning(
                "Session/usage limit detected in agent output; parking provider",
                agent_id=agent_id,
                provider=rate_limited_provider,
                task_id=instance.current_task_id,
            )
            await self._park_provider_unavailable(
                agent_id,
                instance,
                provider=rate_limited_provider,
                retry_after=_RATE_LIMIT_RETRY_AFTER_S,
                kind="rate_limited",
            )
            return True
        auth_missing_provider = await self._provider_auth_park_target(
            agent_id, instance
        )
        if auth_missing_provider is not None:
            logger.warning(
                "Provider auth failure detected in agent output; parking provider",
                agent_id=agent_id,
                provider=auth_missing_provider,
                task_id=instance.current_task_id,
            )
            await self._park_provider_unavailable(
                agent_id,
                instance,
                provider=auth_missing_provider,
                retry_after=_ANTHROPIC_AUTH_RETRY_AFTER_S,
                kind="auth_missing",
            )
            # Marks this agent as the one to watch for the re-arm: only ITS
            # own graceful exit later is proof the credential is back.
            self._auth_parked_agents.add(agent_id)
            if auth_missing_provider not in self._auth_ceo_notified:
                self._auth_ceo_notified.add(auth_missing_provider)
                await self._notify_auth_missing_ceo(
                    provider=auth_missing_provider,
                    agent_id=agent_id,
                    task_id=instance.current_task_id,
                )
            return True
        overloaded_provider = await self._provider_overload_park_target(
            agent_id, instance
        )
        if overloaded_provider is not None:
            logger.warning(
                "Provider overload detected in agent output; parking provider",
                agent_id=agent_id,
                provider=overloaded_provider,
                task_id=instance.current_task_id,
            )
            await self._park_provider_unavailable(
                agent_id,
                instance,
                provider=overloaded_provider,
                retry_after=_OVERLOAD_RETRY_AFTER_S,
                kind="overloaded",
            )
            return True
        return False

    async def _maybe_park_for_known_exit(
        self, agent_id: str, instance: Any, exit_code: int | None
    ) -> bool:
        """Park the agent's provider on a recognized rate-limit/auth exit code.

        Grok, Codex, Gemini, and Kimi each run a one-shot CLI with no live
        SDK/usage signal, so a 429-equivalent or missing-credential exit is
        detected purely from the exit code (see the individual ``_is_*_exit``
        checks). Tries each provider's pair in turn; returns True on the
        first match (having already awaited its ``_park_*`` call), False
        when none match.
        """
        checks = (
            (self._is_grok_rate_limit_exit, self._park_grok_rate_limited),
            (self._is_grok_auth_exit, self._park_grok_auth_unavailable),
            (self._is_codex_rate_limit_exit, self._park_codex_rate_limited),
            (self._is_codex_auth_exit, self._park_codex_auth_unavailable),
            (self._is_gemini_rate_limit_exit, self._park_gemini_rate_limited),
            (self._is_gemini_auth_exit, self._park_gemini_auth_unavailable),
            (self._is_kimi_rate_limit_exit, self._park_kimi_rate_limited),
            (self._is_kimi_auth_exit, self._park_kimi_auth_unavailable),
        )
        for is_exit, park in checks:
            if is_exit(instance, exit_code):
                await park(agent_id, instance)
                return True
        return False

    async def _handle_stopped_container(
        self, agent_id: str, instance: Any, exit_code: int | None
    ) -> None:
        """Update state + auto-restart only when the exit was non-zero.

        Graceful exits (exit 0 — agent called i_am_idle)
        were treated as crashes by the old logic. The health check bumped
        error_count and respawned the agent with the prior task_id even if
        the task had since moved into a state the role can't claim from
        (e.g. QA → needs_revision). Now: clean exits reset error_count and
        do nothing; non-zero exits keep the existing crash-retry behaviour.
        """
        cid = instance.container_id[:12] if instance.container_id else None
        # Grok/Codex/Gemini/Kimi rate-limit + auth-missing parking: each
        # one-shot CLI mirrors the same "recognized exit code -> park the
        # provider" shape (see the individual _is_*_exit / _park_* pairs), so
        # a single dispatch loop replaces what would otherwise be 8
        # near-identical early returns (keeps this function's branching flat
        # for PLR0911).
        if await self._maybe_park_for_known_exit(agent_id, instance, exit_code):
            return
        graceful = exit_code == 0
        # Park the provider on a session/usage limit or a server overload detected
        # in the dead container's output instead of crash-retrying into it. The
        # probe-resume loop revives the task when the limit lifts / overload clears.
        if await self._maybe_park_for_exit_error(agent_id, instance, graceful):
            return
        if graceful:
            logger.info(
                "Agent container exited gracefully",
                agent_id=agent_id,
                container_id=cid,
                exit_code=exit_code,
            )
        else:
            await self._log_stopped_container(agent_id, cid, exit_code)
        # The agent self-exited (a graceful i_am_idle shutdown, or a crash), so
        # stop_agent() — which normally finalizes — was never called. Finalize
        # here to capture token usage from the transcript; otherwise the
        # spawn-session row is left open with zero tokens.
        await self._finalize_spawn_session(
            agent_id, exit_reason="completed" if graceful else "crashed"
        )
        instance.state = AgentState.OFFLINE
        instance.container_id = None
        if graceful:
            instance.error_count = 0
            # Re-arm the auth-missing CEO notification only when THIS agent
            # was itself auth-parked and has now exited gracefully - proof
            # the credential is back. An unrelated agent of the same
            # provider finishing normally mid-outage must not clear the
            # flag, or the next auth crash pages the CEO again for the same
            # still-dead credential.
            if agent_id in self._auth_parked_agents:
                self._auth_parked_agents.discard(agent_id)
                provider_type = (
                    instance.config.provider_type if instance.config else None
                )
                if provider_type is not None:
                    self._auth_ceo_notified.discard(provider_type)
            return
        await self._crash_retry_or_escalate(agent_id, instance)

    async def _log_stopped_container(
        self, agent_id: str, container_id: str | None, exit_code: int | None
    ) -> None:
        """Log a non-graceful exit, attributed via the expected-stop breadcrumb.

        A fresh breadcrumb (recorded by an orchestrator-initiated stop/kill
        path — see ``_record_expected_stop``) means this death is explained:
        log it at info as "(expected)" so the warning line stays meaningful
        for genuinely unattributed SIGTERMs/crashes. Inspect diagnostics are
        best-effort and never block the log line.
        """
        reason = self._consume_expected_stop(agent_id)
        diagnostics = await self._inspect_exit_diagnostics(f"roboco-agent-{agent_id}")
        expected = reason != "none_recorded"
        log = logger.info if expected else logger.warning
        log(
            "Agent container stopped (expected)"
            if expected
            else "Agent container stopped unexpectedly",
            agent_id=agent_id,
            container_id=container_id,
            exit_code=exit_code,
            expected_stop_reason=reason,
            **diagnostics,
        )

    async def _crash_retry_or_escalate(self, agent_id: str, instance: Any) -> None:
        """A crashed (non-graceful) agent: auto-restart up to a cap, then escalate.

        Bumps error_count and respawns while under the cap; at exactly the cap
        escalates once to humans (subsequent crashes stay quiet to avoid spam).
        """
        from roboco.services.maintenance_pause import PauseScope

        # While the fleet is dispatch-paused a crash must NOT respawn (the
        # dispatcher and the reaper both skip paused fleets): the container
        # stays offline and error_count is left untouched so the crash after
        # resume gets a full retry budget instead of inheriting this one.
        if await self._is_paused(PauseScope.DISPATCH):
            logger.info(
                "Crashed agent left offline: dispatch pause is armed",
                agent_id=agent_id,
                task_id=instance.current_task_id,
            )
            return
        instance.error_count += 1
        max_retries = 3
        if instance.error_count < max_retries:
            logger.info("Auto-restarting crashed agent", agent_id=agent_id)
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=instance.current_task_id,
                git_context=(instance.config.git_context if instance.config else None),
                spawned_by="_crash_retry_or_escalate",
            )
        elif instance.error_count == max_retries:
            # Exactly at the threshold — escalate once to humans so a
            # stranded agent doesn't die silently. Subsequent crashes
            # stay quiet to avoid notification spam.
            logger.error(
                "Agent exceeded max restart attempts; escalating",
                agent_id=agent_id,
                error_count=instance.error_count,
                task_id=instance.current_task_id,
            )
            await self._notify_agent_stranded(
                agent_id=agent_id,
                error_count=instance.error_count,
                task_id=instance.current_task_id,
            )

    async def _notify_agent_stranded(
        self,
        agent_id: str,
        error_count: int,
        task_id: str | None,
    ) -> None:
        """Create a notification for humans when an agent can't be restarted.

        Posts a high-priority notification addressed to the auditor and CEO.
        Fire-and-forget: the agent is already dead; don't let our own failure
        stop the health loop.
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import NotificationTable
            from roboco.models.base import (
                AgentRole,
                NotificationPriority,
                NotificationType,
            )
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )
            from roboco.services.repositories.query_helpers import get_agent_by_role
            from roboco.utils.converters import require_uuid

            session_factory = get_session_factory()
            async with session_factory() as db:
                auditor = await get_agent_by_role(db, AgentRole.AUDITOR)
                ceo = await get_agent_by_role(db, AgentRole.CEO)
                recipients = [a.id for a in (auditor, ceo) if a is not None]
                if not recipients:
                    logger.warning(
                        "No auditor/ceo found for stranded-agent notification",
                        agent_id=agent_id,
                    )
                    return
                # recipients is non-empty (guarded above) and already holds the
                # non-None ids in auditor-then-ceo order — its first entry is the
                # same value as `auditor.id if auditor else ceo.id`, without the
                # union-narrowing mypy can't prove.
                from_agent = recipients[0]
                notification = NotificationTable(
                    type=NotificationType.ALERT,
                    priority=NotificationPriority.HIGH,
                    from_agent=from_agent,
                    to_agents=recipients,
                    subject=f"Agent stranded: {agent_id}",
                    body=(
                        f"Agent '{agent_id}' exceeded max restart attempts "
                        f"({error_count}) and will not auto-recover. "
                        f"Task: {task_id or 'none'}. Manual intervention needed."
                    ),
                    requires_ack=True,
                )
                db.add(notification)
                await db.flush()
                delivery = get_notification_delivery_service(db)
                await delivery.deliver(require_uuid(notification.id))
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to send stranded-agent notification",
                agent_id=agent_id,
                error=str(e),
            )

    @staticmethod
    def _is_grok_rate_limit_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot grok container that exited 75 (xAI 429)."""
        from roboco.models.base import ModelProvider

        return (
            exit_code == _GROK_RATE_LIMIT_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.GROK.value
        )

    @staticmethod
    def _is_grok_auth_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot grok container that exited 78 (auth missing/expired).

        The entrypoint runs ``grok_auth --check`` as a backstop and exits 78
        (EX_CONFIG) when the access token is missing or expired — the CLI cannot
        refresh it headlessly and would otherwise hang at an interactive login
        prompt. See ``_GROK_AUTH_EXIT_CODE`` for the full rationale (F041).
        """
        from roboco.models.base import ModelProvider

        return (
            exit_code == _GROK_AUTH_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.GROK.value
        )

    @staticmethod
    def _is_codex_rate_limit_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot codex container that exited 75 (OpenAI 429)."""
        from roboco.models.base import ModelProvider

        return (
            exit_code == _CODEX_RATE_LIMIT_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.OPENAI.value
        )

    @staticmethod
    def _is_codex_auth_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot codex container that exited 78 (auth missing/expired).

        The entrypoint runs ``codex_auth --check`` as a backstop and exits 78
        when the ChatGPT-subscription token is missing or expired — see
        ``_CODEX_AUTH_EXIT_CODE``.
        """
        from roboco.models.base import ModelProvider

        return (
            exit_code == _CODEX_AUTH_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.OPENAI.value
        )

    @staticmethod
    def _is_gemini_rate_limit_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot gemini container that exited 75 (quota/rate-limit)."""
        from roboco.models.base import ModelProvider

        return (
            exit_code == _GEMINI_RATE_LIMIT_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.GEMINI.value
        )

    @staticmethod
    def _is_gemini_auth_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot gemini container that exited 41 (OAuth credential gone).

        The entrypoint's preflight refuses to run (exit 41 — the CLI's own
        dedicated auth-failure code) when the mounted ``~/.gemini/oauth_creds.json``
        is missing/empty. See ``_GEMINI_AUTH_EXIT_CODE`` for the full rationale.
        """
        from roboco.models.base import ModelProvider

        return (
            exit_code == _GEMINI_AUTH_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.GEMINI.value
        )

    @staticmethod
    def _is_kimi_rate_limit_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot kimi container that exited 75 (Moonshot 429/quota)."""
        from roboco.models.base import ModelProvider

        return (
            exit_code == _KIMI_RATE_LIMIT_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.KIMI.value
        )

    @staticmethod
    def _is_kimi_auth_exit(instance: Any, exit_code: int | None) -> bool:
        """True for a one-shot kimi container that exited 78 (auth missing/expired).

        The entrypoint runs ``kimi_cli_config --check`` as a backstop and
        exits 78 when the symlinked-in subscription credential is missing or
        expired — see ``_KIMI_AUTH_EXIT_CODE``.
        """
        from roboco.models.base import ModelProvider

        return (
            exit_code == _KIMI_AUTH_EXIT_CODE
            and instance.config is not None
            and instance.config.provider_type == ModelProvider.KIMI.value
        )

    @staticmethod
    async def _tail_container_logs(container_name: str, lines: int = 80) -> str:
        """Return the last ``lines`` of a container's combined output, '' on error.

        The container is still present at exit (agents run detached, not
        ``--rm``), so ``docker logs`` can read what the dead run printed.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "logs",
                "--tail",
                str(lines),
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
        except Exception:
            return ""
        return out.decode(errors="replace")

    def _transcript_tail_text(self, agent_id: str, lines: int = 80) -> str:
        """Return the last ``lines`` of the newest Claude transcript for *agent_id*.

        The SDK server redirects its runtime log to ``/tmp/sdk-server.log`` inside
        the container, so session-limit markers such as "hit your session limit"
        and "five_hour" do not reach ``docker logs``. The durable Claude
        transcript on the host (mounted into the orchestrator at ``~/.claude``)
        contains those same events, so we search it as a fallback when deciding
        whether to park the provider. Returns "" when no transcript is found or it
        cannot be read.
        """
        from pathlib import Path

        projects = Path.home() / ".claude" / "projects"
        try:
            jsonl = [
                f
                for d in projects.glob(f"*-{agent_id}")
                if d.is_dir()
                for f in d.glob("*.jsonl")
            ]
            if not jsonl:
                return ""
            newest = max(jsonl, key=lambda f: f.stat().st_mtime)
            text = newest.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return ""

    async def _provider_overload_park_target(
        self, agent_id: str, instance: Any
    ) -> str | None:
        """Provider to park if this dead run hit a persistent overload, else None.

        Data-driven by ``_OVERLOAD_MARKERS_BY_PROVIDER``: only providers with
        specific overload markers are candidates, so grok (its own exit-75
        detector) and unhandled providers stay on crash-retry. Returns the
        matched provider value, or None when the feature is disabled, the
        provider has no overload markers, or the output holds no marker.
        """
        if not settings.overload_break_enabled:
            return None
        provider_type = instance.config.provider_type if instance.config else None
        markers = (
            _OVERLOAD_MARKERS_BY_PROVIDER.get(provider_type) if provider_type else None
        )
        if markers is None:
            return None
        tail = await self._tail_container_logs(f"roboco-agent-{agent_id}")
        # The SDK server writes model-API errors to /tmp/sdk-server.log, not
        # stdout, so the overload marker may appear only in the durable Claude
        # transcript; without it an overload is missed and the agent
        # crash-respawns straight back into it.
        transcript_tail = self._transcript_tail_text(agent_id)
        lowered = (tail + "\n" + transcript_tail).lower()
        if any(marker in lowered for marker in markers):
            return provider_type
        return None

    async def _provider_rate_limit_park_target(
        self, agent_id: str, instance: Any
    ) -> str | None:
        """Provider to park if this dead run hit a session/usage limit, else None.

        Mirrors ``_provider_overload_park_target`` but matches the Claude
        session ("5-hour") limit AND the ollama.com weekly limit
        (``glm-5.3:cloud``), both of which surface as a 429 the SDK does not
        retry. Data-driven by ``_RATE_LIMIT_MARKERS_BY_PROVIDER``; returns the
        matched provider value or None. Gated so a misfire can be turned off
        without a redeploy.
        """
        if not settings.overload_break_enabled:
            return None
        provider_type = instance.config.provider_type if instance.config else None
        markers = (
            _RATE_LIMIT_MARKERS_BY_PROVIDER.get(provider_type)
            if provider_type
            else None
        )
        if markers is None:
            return None
        tail = await self._tail_container_logs(f"roboco-agent-{agent_id}")
        # The SDK server writes to /tmp/sdk-server.log, not stdout, so the
        # session-limit markers may not appear in docker logs. Search the durable
        # Claude transcript on the host as well.
        transcript_tail = self._transcript_tail_text(agent_id)
        lowered = (tail + "\n" + transcript_tail).lower()
        if any(marker in lowered for marker in markers):
            return provider_type
        return None

    async def _provider_auth_park_target(
        self, agent_id: str, instance: Any
    ) -> str | None:
        """Provider to park if this dead run hit an expired host auth credential.

        Anthropic only: the Claude Code OAuth session backing the mounted
        ``~/.claude`` credential store expired and could not be refreshed. With
        no ``settings.anthropic_api_key`` the recovery probe
        (``_probe_target``) cannot run and falls back to time-expiry
        optimism, so a still-dead credential re-parks once per retry window
        (bounded burn, zero tokens) until the operator logs in.
        """
        from roboco.models.base import ModelProvider

        if not settings.overload_break_enabled:
            return None
        provider_type = instance.config.provider_type if instance.config else None
        if provider_type != ModelProvider.ANTHROPIC.value:
            return None
        tail = await self._tail_container_logs(f"roboco-agent-{agent_id}")
        transcript_tail = self._transcript_tail_text(agent_id)
        lowered = (tail + "\n" + transcript_tail).lower()
        if any(marker in lowered for marker in _ANTHROPIC_AUTH_MARKERS):
            return ModelProvider.ANTHROPIC.value
        return None

    async def _park_provider_unavailable(
        self,
        agent_id: str,
        instance: Any,
        *,
        provider: str,
        retry_after: float,
        kind: str,
    ) -> None:
        """Park an agent whose run ended because its provider is unavailable.

        Covers both a 429 rate limit and a persistent 5xx overload. Finalize
        the session for usage capture, mark the instance OFFLINE WITHOUT
        counting a crash (so it isn't escalated as stranded), and activate the
        provider's tracker so the spawn guard suppresses re-spawns until the
        probe-resume loop clears it. The task stays claimed/in_progress and is
        retried when the provider recovers.
        """
        await self._finalize_spawn_session(agent_id, exit_reason=kind)
        instance.state = AgentState.OFFLINE
        instance.container_id = None
        instance.error_count = 0  # provider unavailability is not a crash
        try:
            await self._make_tracker(provider).activate(
                retry_after=retry_after,
                affected_agents=[agent_id],
                kind=kind,
            )
        except Exception as exc:
            logger.warning(
                "failed to park provider-unavailable state",
                provider=provider,
                kind=kind,
                error=str(exc),
            )
        # Register a WaitingRecord so the probe-resume loop can revive this
        # agent when the provider recovers; without it recovery falls to the
        # 600s stale-claim reaper instead of the probe-success path the parking
        # design relies on. Persisted so a restart still resolves the wait.
        # We do NOT call ``mark_waiting_long`` — the container is already dead,
        # and parking keeps OFFLINE so the reaper's live-skip / health loop
        # ignore it.
        task_id = str(instance.current_task_id) if instance.current_task_id else None
        record = WaitingRecord(
            agent_id=agent_id,
            task_id=task_id,
            waiting_for="rate_limit_lifted",
            waiting_since=datetime.now(UTC),
            context={"provider": provider, "kind": kind},
        )
        self._waiting_records[agent_id] = record
        with contextlib.suppress(Exception):
            await self._persist_waiting_record(record)
        logger.warning(
            "Provider unavailable; parked (task retried when it recovers)",
            provider=provider,
            kind=kind,
            agent_id=agent_id,
            task_id=instance.current_task_id,
        )

    async def _park_grok_rate_limited(self, agent_id: str, instance: Any) -> None:
        """Park a grok agent whose run hit an xAI 429 (entrypoint exit 75).

        F097: grok has no real recovery probe, so the probe loop clears a grok
        park optimistically on a timer — a cleared park dispatches a fresh
        grok agent that hits the still-active xAI 429, exits 75, and re-parks.
        Without a backoff this is a flat ~90s crash-retry cycle for the whole
        xAI rate-limit window. Back the re-park retry_after off exponentially
        within one episode (60 -> 120 -> 240 -> ... capped) so the churn
        dampens. A gap past ``_GROK_REPARK_EPISODE_GAP_S`` (no re-park for that
        long => the rate limit actually lifted) starts a fresh episode at the
        base retry_after, so recovery latency isn't penalized across episodes.
        """
        from roboco.models.base import ModelProvider

        now = datetime.now(UTC)
        last = self._grok_last_park_at
        if (
            last is not None
            and (now - last).total_seconds() < _GROK_REPARK_EPISODE_GAP_S
        ):
            self._grok_repark_count += 1
        else:
            self._grok_repark_count = 0
        self._grok_last_park_at = now
        backoff = 2 ** min(self._grok_repark_count, _GROK_REPARK_BACKOFF_CAP)
        retry_after = _GROK_RATE_LIMIT_RETRY_AFTER_S * backoff
        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.GROK.value,
            retry_after=retry_after,
            kind="rate_limited",
        )

    async def _park_grok_auth_unavailable(self, agent_id: str, instance: Any) -> None:
        """Park a grok agent whose token was missing/expired (entrypoint exit 78).

        Same park-and-probe shape as the 429 exit-75 path, but with
        ``kind="auth_missing"``: the agent cannot start without a valid token, so
        crash-retrying burns tokens for zero progress. The probe-resume loop
        revives the task once ``grok_auth.refresh_if_stale`` mints a fresh token
        (run once per dispatch tick); if still expired, the next exit 78 re-parks
        (no token burn). See ``_GROK_AUTH_EXIT_CODE`` (F041).
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.GROK.value,
            retry_after=_GROK_AUTH_RETRY_AFTER_S,
            kind="auth_missing",
        )

    async def _park_codex_rate_limited(self, agent_id: str, instance: Any) -> None:
        """Park a codex agent whose run hit an OpenAI 429 (entrypoint exit 75).

        Flat retry_after (no exponential re-park backoff like grok's — see
        ``_CODEX_RATE_LIMIT_EXIT_CODE``): add the same backoff bookkeeping if
        Codex is observed re-parking in a tight cycle in practice.
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.OPENAI.value,
            retry_after=_CODEX_RATE_LIMIT_RETRY_AFTER_S,
            kind="rate_limited",
        )

    async def _park_codex_auth_unavailable(self, agent_id: str, instance: Any) -> None:
        """Park a codex agent whose token was missing/expired (entrypoint exit 78).

        Same park-and-probe shape as the grok auth path: the agent cannot
        start without a valid token, so crash-retrying burns tokens for zero
        progress. The dispatcher loop's ``_refresh_codex_auth`` revives the
        task once ``codex_auth.refresh_if_stale`` mints a fresh token.
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.OPENAI.value,
            retry_after=_CODEX_AUTH_RETRY_AFTER_S,
            kind="auth_missing",
        )

    async def _park_gemini_rate_limited(self, agent_id: str, instance: Any) -> None:
        """Park a gemini agent whose run hit a quota/rate-limit (entrypoint exit 75).

        Same repark-backoff shape as ``_park_grok_rate_limited`` (F097): Gemini
        has no real recovery probe either (an OAuth-login daily quota cap has
        no cheap balance-check API), so the probe loop clears the park
        optimistically on a timer and a still-active quota re-parks. Back the
        re-park ``retry_after`` off exponentially within one episode so the
        churn dampens; a gap past ``_GEMINI_REPARK_EPISODE_GAP_S`` starts a
        fresh episode at the base retry_after.
        """
        from roboco.models.base import ModelProvider

        now = datetime.now(UTC)
        last = self._gemini_last_park_at
        if (
            last is not None
            and (now - last).total_seconds() < _GEMINI_REPARK_EPISODE_GAP_S
        ):
            self._gemini_repark_count += 1
        else:
            self._gemini_repark_count = 0
        self._gemini_last_park_at = now
        backoff = 2 ** min(self._gemini_repark_count, _GEMINI_REPARK_BACKOFF_CAP)
        base = getattr(self, "_gemini_rate_limit_retry_after_s", 60.0)
        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.GEMINI.value,
            retry_after=base * backoff,
            kind="rate_limited",
        )

    async def _park_gemini_auth_unavailable(self, agent_id: str, instance: Any) -> None:
        """Park a gemini agent whose OAuth credential was missing (entrypoint exit 41).

        Unlike grok's exit-78 auth park, no orchestrator-side refresher daemon
        proactively mints a new token here (see ``_GEMINI_AUTH_EXIT_CODE`` and
        ``roboco.llm.providers.gemini``'s module docstring for why none is
        needed for a genuinely PRESENT-but-stale credential); a genuinely
        missing/invalid one re-parks flat until an operator fixes it on the
        host — same flat (no-backoff) shape as grok's own auth park.
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.GEMINI.value,
            retry_after=getattr(self, "_gemini_auth_retry_after_s", 60.0),
            kind="auth_missing",
        )

    async def _park_kimi_rate_limited(self, agent_id: str, instance: Any) -> None:
        """Park a kimi agent whose run hit a Moonshot 429/quota (entrypoint exit 75).

        Flat retry_after (no exponential re-park backoff like grok's/gemini's
        — see ``_KIMI_RATE_LIMIT_EXIT_CODE``): add the same backoff
        bookkeeping if Kimi is observed re-parking in a tight cycle in
        practice. The base itself is a tunable Setting (gemini's pattern),
        not a hardcoded constant (codex's pattern).
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.KIMI.value,
            retry_after=getattr(self, "_kimi_rate_limit_retry_after_s", 60.0),
            kind="rate_limited",
        )

    async def _park_kimi_auth_unavailable(self, agent_id: str, instance: Any) -> None:
        """Park a kimi agent whose credential was missing/expired (entrypoint exit 78).

        Same park-and-probe shape as the codex auth path: the agent cannot
        start without a valid credential, so crash-retrying burns tokens for
        zero progress. Unlike codex/grok, no orchestrator-side refresher
        daemon proactively mints a new token here — D2 resolved Kimi's
        refresh token as rotation-with-short-reuse-grace over ONE shared RW
        auth mount (symlinked into every container, not copied — see
        roboco.llm.providers.kimi), refreshed IN-PROCESS by the CLI itself
        through its own cross-process lock — so a genuinely bad/missing
        credential re-parks flat until an operator fixes it on the host
        (``kimi login``), exactly like gemini's own auth-exit park.
        """
        from roboco.models.base import ModelProvider

        await self._park_provider_unavailable(
            agent_id,
            instance,
            provider=ModelProvider.KIMI.value,
            retry_after=getattr(self, "_kimi_auth_retry_after_s", 60.0),
            kind="auth_missing",
        )

    async def _notify_auth_missing_ceo(
        self, provider: str, agent_id: str, task_id: str | None
    ) -> None:
        """Send a high-priority notification to the CEO about a dead host credential.

        Fires once per expiry episode (caller gates via ``_auth_ceo_notified``).
        Same direct DB insert + delivery.deliver() pattern as
        ``_notify_rate_limit_ceo``, but a distinct type: this is a blocker on
        the fleet, not a transient rate limit.
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import NotificationTable
            from roboco.models.base import (
                AgentRole,
                NotificationPriority,
                NotificationType,
            )
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )
            from roboco.services.repositories.query_helpers import get_agent_by_role
            from roboco.utils.converters import require_uuid

            session_factory = get_session_factory()
            async with session_factory() as db:
                ceo = await get_agent_by_role(db, AgentRole.CEO)
                if ceo is None:
                    logger.warning(
                        "CEO agent not found; skipping auth-missing CEO notification",
                        provider=provider,
                    )
                    return
                notification = NotificationTable(
                    type=NotificationType.BLOCKER_ESCALATION,
                    priority=NotificationPriority.HIGH,
                    from_agent=ceo.id,
                    to_agents=[ceo.id],
                    subject=(
                        "Claude login expired on the orchestrator host: "
                        "Anthropic agents parked"
                    ),
                    body=(
                        "The host's Claude Code OAuth session expired and could "
                        f"not be refreshed (first hit by agent '{agent_id}' on "
                        f"task {task_id or 'none'}). Every Anthropic agent is "
                        "parked; no tokens are burned. Fix: an operator runs "
                        "`claude login` (or `claude setup-token`) on the host as "
                        "the user whose ~/.claude is mounted into the agent "
                        "containers. The fleet resumes on its own afterwards."
                    ),
                    requires_ack=True,
                )
                db.add(notification)
                await db.flush()
                delivery = get_notification_delivery_service(db)
                await delivery.deliver(require_uuid(notification.id))
                await db.commit()
            logger.info(
                "Auth-missing CEO notification sent",
                provider=provider,
                agent_id=agent_id,
                task_id=task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to send auth-missing CEO notification",
                provider=provider,
                agent_id=agent_id,
                error=str(e),
            )
