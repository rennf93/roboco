"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: interactive_sessions)."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.agents_config import (
    get_agent_role,
    get_agent_team,
)
from roboco.config import settings
from roboco.models.runtime import (
    AgentInstance,
)
from roboco.runtime.compose_labels import compose_label_args
from roboco.runtime.orchestrator import (
    _GROK_INTERACTIVE_DOCKERFILES,
    _INTAKE_WORKSPACE_AMBIENT,
    AGENT_BASE_IMAGE,
    AGENT_NETWORK,
    CLAUDE_AUTH_HOST_PATH,
    DATA_HOST_PATH,
    GROK_PROMPTER_IMAGE,
    GROK_SECRETARY_IMAGE,
    INTAKE_AGENT_ID,
    PROJECT_HOST_PATH,
    SECRETARY_AGENT_ID,
    AgentConfig,
    AgentState,
    _agent_workspace_path,
    _IntakeRunSpec,
    _reject_interactive_unsupported_provider,
    _resolve_agent_cli_model,
    _SecretaryRunSpec,
    _SpawnAbortedDuringShutdown,
    get_agent_image,
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


class InteractiveSessionsEngine(_Base):
    """Mixin holding the "interactive_sessions" methods moved out of
    AgentOrchestrator."""

    async def _ensure_grok_interactive_image(self, image: str) -> None:
        """Ensure a Grok interactive image and its base→runtime chain exist.

        The grok-prompter / grok-secretary images build FROM roboco-agent-grok,
        which builds FROM the agent base, so the whole chain must be present
        before a local build of the interactive image can succeed (on the
        registry path each is already pulled and this just verifies presence).
        """
        if PROJECT_HOST_PATH:
            build_context = PROJECT_HOST_PATH
            docker_dir = f"{PROJECT_HOST_PATH}/docker"
        else:
            build_context = str(self.project_root)
            docker_dir = str(self.project_root / "docker")
        chain = [
            (AGENT_BASE_IMAGE, "agent-base.Dockerfile"),
            ("roboco-agent-grok", "agent-grok.Dockerfile"),
            (image, _GROK_INTERACTIVE_DOCKERFILES[image]),
        ]
        for img, dockerfile in chain:
            await self._ensure_image_present(
                img, f"{docker_dir}/{dockerfile}", build_context
            )

    @staticmethod
    def _grok_usage_dir(agent_id: str) -> Path:
        """Per-agent grok usage dir under :meth:`_grok_usage_root`.

        Single source of truth for BOTH the pre-create/mount side
        (``_ensure_grok_usage_dir``) and the finalize read side
        (``_grok_usage_json``) so they can never drift. ``agent_id`` is validated
        as a single safe path segment first — ``_safe_agent_path_segment`` rejects
        ``.`` / ``..`` / separators / NUL so a bad id raises rather than silently
        remapping or traversing. The read side additionally reduces the id to its
        final path component (``os.path.basename``) — the CodeQL-recognized
        path-injection barrier.
        """
        return AgentOrchestrator._grok_usage_root() / (
            AgentOrchestrator._safe_agent_path_segment(agent_id)
        )

    def _ensure_grok_usage_dir(self, agent_id: str) -> None:
        """Pre-create the agent's grok usage dir (world-writable) before the mount.

        On Linux, ``docker run -v`` auto-creates a MISSING bind source as
        ``root:root``, so the non-root ``agent`` user EACCESes when the grok
        entrypoint / interactive driver writes ``usage.json`` there. Creating the
        dir ``0777`` first makes the mounted dir writable regardless of the agent
        uid; the orchestrator (root) can still read it back at finalize.
        """
        target = self._grok_usage_dir(agent_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o777)
        except OSError as exc:
            logger.warning(
                "could not pre-create grok usage dir; grok agent may EACCES",
                agent_id=agent_id,
                path=str(target),
                error=str(exc),
            )

    @staticmethod
    def _codex_usage_dir(agent_id: str) -> Path:
        """Per-agent codex usage dir under :meth:`_codex_usage_root`.

        Single source of truth for BOTH the pre-create/mount side
        (``_ensure_codex_usage_dir``) and the finalize read side
        (``_codex_usage_json``), mirroring ``_grok_usage_dir``.
        """
        return AgentOrchestrator._codex_usage_root() / (
            AgentOrchestrator._safe_agent_path_segment(agent_id)
        )

    def _ensure_codex_usage_dir(self, agent_id: str) -> None:
        """Pre-create the agent's codex usage dir (world-writable) before the mount.

        Same EACCES concern as ``_ensure_grok_usage_dir``: a missing bind
        source is auto-created ``root:root`` on Linux, which the non-root
        ``agent`` user can't write into.
        """
        target = self._codex_usage_dir(agent_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o777)
        except OSError as exc:
            logger.warning(
                "could not pre-create codex usage dir; codex agent may EACCES",
                agent_id=agent_id,
                path=str(target),
                error=str(exc),
            )

    @staticmethod
    def _gemini_usage_dir(agent_id: str) -> Path:
        """Per-agent gemini usage dir under :meth:`_gemini_usage_root`.

        Single source of truth for BOTH the pre-create/mount side
        (``_ensure_gemini_usage_dir``) and the finalize read side
        (``_gemini_usage_json``) — see :meth:`_grok_usage_dir`'s docstring for
        the ``_safe_agent_path_segment`` traversal-rejection rationale, shared
        verbatim here.
        """
        return AgentOrchestrator._gemini_usage_root() / (
            AgentOrchestrator._safe_agent_path_segment(agent_id)
        )

    def _ensure_gemini_usage_dir(self, agent_id: str) -> None:
        """Pre-create the agent's gemini usage dir (world-writable) before the mount.

        Same EACCES rationale as :meth:`_ensure_grok_usage_dir`: a missing
        Linux bind-mount source is auto-created ``root:root``, so the non-root
        ``agent`` user would fail to write ``usage.json`` there without this.
        """
        target = self._gemini_usage_dir(agent_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o777)
        except OSError as exc:
            logger.warning(
                "could not pre-create gemini usage dir; gemini agent may EACCES",
                agent_id=agent_id,
                path=str(target),
                error=str(exc),
            )

    @staticmethod
    def _kimi_usage_dir(agent_id: str) -> Path:
        """Per-agent kimi usage dir under :meth:`_kimi_usage_root`.

        Single source of truth for BOTH the pre-create/mount side
        (``_ensure_kimi_usage_dir``) and the finalize read side
        (``_kimi_usage_json``), mirroring ``_grok_usage_dir``.
        """
        return AgentOrchestrator._kimi_usage_root() / (
            AgentOrchestrator._safe_agent_path_segment(agent_id)
        )

    def _ensure_kimi_usage_dir(self, agent_id: str) -> None:
        """Pre-create the agent's kimi usage dir (world-writable) before the mount.

        Same EACCES concern as ``_ensure_grok_usage_dir``: a missing bind
        source is auto-created ``root:root`` on Linux, which the non-root
        ``agent`` user can't write into.
        """
        target = self._kimi_usage_dir(agent_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o777)
        except OSError as exc:
            logger.warning(
                "could not pre-create kimi usage dir; kimi agent may EACCES",
                agent_id=agent_id,
                path=str(target),
                error=str(exc),
            )

    async def _resolve_history_digest_ambient(
        self,
        project_slug: str | None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
    ) -> str | None:
        """Resolve the prompter's task-history-digest ambient block for this scope.

        Covers all three intake scopes including ``project_ids`` (a MegaTask) —
        the digest is meant to span every project the intake agent is reading.
        Best-effort: returns None on any failure or empty scope so history
        resolution can never block a spawn.
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.services.prompter import history_digest_layer

            factory = get_session_factory()
            async with factory() as db:
                projects = await self._resolve_history_digest_projects(
                    db,
                    project_slug=project_slug,
                    product_id=product_id,
                    project_ids=project_ids,
                )
                return await history_digest_layer(db, projects)
        except Exception as exc:
            logger.warning(
                "History digest ambient resolution failed (non-fatal)",
                project_slug=project_slug,
                error=str(exc),
            )
            return None

    @staticmethod
    async def _resolve_history_digest_projects(
        db: Any,
        *,
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None,
    ) -> list[Any]:
        """The in-scope ProjectTable rows for the history digest — single repo,
        product (all cell projects), or an explicit MegaTask project_ids set."""
        if project_ids:
            return await AgentOrchestrator._projects_by_ids(db, project_ids)
        if product_id is not None:
            return await AgentOrchestrator._ambient_product_projects(db, product_id)
        if project_slug:
            from roboco.services.project import get_project_service

            project = await get_project_service(db).get_by_slug(project_slug)
            return [project] if project is not None else []
        return []

    async def _resolve_intake_ambient(
        self,
        project_slug: str | None,
        *,
        product_id: str | None,
        project_ids: list[str] | None,
    ) -> str | None:
        """The intake spawn's full ambient block: conventions + history digest,
        joined with ``compose_prompt``'s own layer separator."""
        conventions_ambient = await self._resolve_conventions_ambient(
            project_slug, product_id=product_id, project_ids=project_ids
        )
        history_ambient = await self._resolve_history_digest_ambient(
            project_slug, product_id=product_id, project_ids=project_ids
        )
        return (
            "\n\n---\n\n".join(
                part
                for part in (
                    _INTAKE_WORKSPACE_AMBIENT,
                    conventions_ambient,
                    history_ambient,
                )
                if part
            )
            or None
        )

    @staticmethod
    def _require_one_intake_scope(
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None,
    ) -> None:
        """Exactly one intake scope: a single project, a product, or a MegaTask's
        explicit project set."""
        chosen = sum(1 for scope in (project_slug, product_id, project_ids) if scope)
        if chosen != 1:
            raise ValueError(
                "intake scope requires exactly one of project_slug / product_id"
                " / project_ids"
            )

    async def start_intake_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
        initial_message: str | None = None,
    ) -> None:
        """Non-blocking start: open the relay now, spawn the container in the bg.

        The panel's ``POST /live/start`` returns immediately rather than blocking
        on the workspace clone + first-time image build + ``docker run`` (which
        can exceed the HTTP timeout — the cause of the "Request timed out" the
        panel showed). The panel opens the SSE stream right away; the agent's
        first reply arrives once the container is up. A spawn failure is pushed
        onto the relay as an ``error`` event and closes the session, so the panel
        shows it instead of hanging. Exactly one of ``project_slug`` /
        ``product_id`` / ``project_ids`` (a MegaTask) must be given.
        """
        self._require_one_intake_scope(project_slug, product_id, project_ids)
        self._open_intake_relay(session_id)
        self._schedule_bg(
            self._spawn_intake_container_guarded(
                session_id,
                project_slug=project_slug,
                product_id=product_id,
                project_ids=project_ids,
                initial_message=initial_message,
            )
        )

    async def spawn_intake_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
        initial_message: str | None = None,
    ) -> AgentInstance:
        """Spawn the intake container for one live chat, **synchronously**.

        Opens the relay then clones + launches the container, awaiting the whole
        thing. Prefer ``start_intake_session`` on the request path; this blocking
        variant is for direct/internal callers and tests. Exactly one of
        ``project_slug`` / ``product_id`` / ``project_ids`` (a MegaTask) must be
        given.
        """
        self._require_one_intake_scope(project_slug, product_id, project_ids)
        self._open_intake_relay(session_id)
        return await self._spawn_intake_container(
            session_id,
            project_slug=project_slug,
            product_id=product_id,
            project_ids=project_ids,
            initial_message=initial_message,
        )

    @staticmethod
    def _open_intake_relay(session_id: str) -> None:
        """Register the live relay session so the SSE stream connects immediately."""
        from roboco.services.prompter_live import get_live_registry

        get_live_registry().open(session_id, INTAKE_AGENT_ID)

    async def _spawn_intake_container_guarded(
        self,
        session_id: str,
        *,
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None = None,
        initial_message: str | None,
    ) -> None:
        """Background container spawn; surface failures on the relay, not silently."""
        from roboco.services.prompter_live import get_live_registry

        try:
            await self._spawn_intake_container(
                session_id,
                project_slug=project_slug,
                product_id=product_id,
                project_ids=project_ids,
                initial_message=initial_message,
            )
        except _SpawnAbortedDuringShutdown:
            # Shutdown began mid-spawn; the just-started container was already
            # removed by the raiser. Close the relay silently — shutdown is not a
            # user-facing failure, so no error is pushed to the SSE stream.
            get_live_registry().close(session_id)
            return
        except Exception as exc:
            logger.error(
                "Intake container spawn failed", session_id=session_id, error=str(exc)
            )
            registry = get_live_registry()
            registry.push(
                session_id,
                {"kind": "error", "text": f"Couldn't start the intake agent: {exc}"},
            )
            registry.close(session_id)

    async def _spawn_intake_container(
        self,
        session_id: str,
        *,
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None = None,
        initial_message: str | None,
    ) -> AgentInstance:
        """Clone the scope, launch the SDK-driver container, track the instance.

        The relay must already be open (``_open_intake_relay``). Heavy + slow
        (clone + first-time image build + docker run) — keep it off the request
        path via ``start_intake_session``.

        Serialized by ``_intake_spawn_lock``: the intake agent id is a single
        fixed id, so two concurrent starts would race on the container name and
        the ``_instances`` write, orphaning a container + relay. The lock makes a
        concurrent start wait for the in-flight one to finish (reap + register)
        before it begins its own reap-prior check.
        """
        async with self._intake_spawn_lock:
            # Single live session: reap any prior intake container before spawning.
            if INTAKE_AGENT_ID in self._instances:
                await self.stop_agent(
                    INTAKE_AGENT_ID,
                    graceful=False,
                    stop_reason="intake_respawn_guard",
                )

            from roboco.models.base import ModelProvider

            cwd, cloned = await self._clone_intake_scope(
                project_slug, product_id, project_ids
            )

            ambient = await self._resolve_intake_ambient(
                project_slug, product_id=product_id, project_ids=project_ids
            )
            prompt_path = self._generate_composed_prompt(
                INTAKE_AGENT_ID, ambient=ambient
            )
            route = await self._resolve_agent_route(INTAKE_AGENT_ID)
            _reject_interactive_unsupported_provider(
                INTAKE_AGENT_ID, route.provider_type
            )
            cli_model = _resolve_agent_cli_model(
                route.provider_type.value, route.model_name
            )
            api_url = (
                "http://roboco-orchestrator:8000"
                if PROJECT_HOST_PATH
                else f"http://127.0.0.1:{settings.port}"
            )

            # GROK runs the interactive driver on its own grok-CLI prompter image;
            # every other provider uses the Claude SDK-driver prompter image.
            is_grok = route.provider_type == ModelProvider.GROK
            image = GROK_PROMPTER_IMAGE if is_grok else get_agent_image(INTAKE_AGENT_ID)
            if is_grok:
                await self._ensure_grok_interactive_image(image)
                self._ensure_grok_usage_dir(INTAKE_AGENT_ID)
            else:
                await self._ensure_agent_image(INTAKE_AGENT_ID)
            container_name = f"roboco-agent-{INTAKE_AGENT_ID}"
            await self._remove_container(
                container_name, stop_reason="pre_spawn_stale_clear"
            )

            cmd = self._build_intake_run_cmd(
                _IntakeRunSpec(
                    container_name=container_name,
                    image=image,
                    hosts=self._resolve_intake_host_paths(),
                    session_id=session_id,
                    cwd=cwd,
                    cli_model=cli_model,
                    api_url=api_url,
                    provider_base_url=route.base_url,
                    provider_auth_token=route.auth_token,
                    provider_type=route.provider_type.value,
                    model=route.model_name,
                )
            )
            # Insert before the trailing image element (docker run flags must
            # precede the image, not follow it).
            cmd[-1:-1] = await compose_label_args(INTAKE_AGENT_ID)
            container_id = await self._run_container_cmd(cmd)

            # Shutdown may have begun while this (non-blocking) spawn was in flight
            # — the bg coroutine runs concurrently with stop(). If so, remove the
            # just-started container and abort WITHOUT registering: stop()'s
            # _instances iteration has already run (or is running), so a registration
            # now would land a live container nothing tears down (the orphan). The
            # stop() drain awaits this coroutine, so the abort surfaces cleanly.
            if not self._running:
                await self._remove_container(
                    container_name, stop_reason="spawn_aborted_shutdown"
                )
                raise _SpawnAbortedDuringShutdown(INTAKE_AGENT_ID)

            config = AgentConfig(
                agent_id=INTAKE_AGENT_ID,
                blueprint_path=prompt_path,
                model=route.model_name,
                git_context=None,
                provider_type=route.provider_type.value,
            )
            instance = AgentInstance(
                agent_id=INTAKE_AGENT_ID,
                state=AgentState.ACTIVE,
                config=config,
                current_task_id=None,
            )
            instance.container_id = container_id
            instance.started_at = datetime.now(UTC)
            instance.last_activity = datetime.now(UTC)
            self._instances[INTAKE_AGENT_ID] = instance

            # Record a usage session (task_id=None) and pin its id on the instance
            # so the reap finalizer can look up token usage — without this an
            # interactive session finalizes at 0 tokens / $0 (the GROK path reads the
            # captured usage.json; the Claude path reads the transcript). Mirrors
            # _launch_spawn.
            usage_session_id = await self._record_spawn_session(config, None)
            if usage_session_id is not None:
                instance.usage_session_id = usage_session_id

            # The relay was already opened on the request path
            # (start_intake_session / spawn_intake_session) BEFORE the panel
            # connected its SSE stream. Do NOT re-open here: a second open would
            # swap in a fresh queue and orphan that already-connected stream (the
            # agent's replies would push to the new queue while the browser keeps
            # reading the old one). open() is idempotent now as a guard, but the
            # redundant call is gone regardless.
            logger.info(
                "Intake session spawned",
                session_id=session_id,
                container_id=container_id[:12],
                cwd=cwd,
                repos=len(cloned),
            )
            self._fire_audit(
                event_type="agent.spawned",
                agent_slug=INTAKE_AGENT_ID,
                details={"session_id": session_id, "cwd": cwd, "repos": cloned},
            )

            if initial_message:
                self._schedule_intake_first_message(
                    session_id, initial_message, persist=True
                )
            return instance

    async def start_secretary_session(
        self, session_id: str, *, initial_message: str | None = None
    ) -> None:
        """Non-blocking start: open the relay now, spawn the container in the bg."""
        from roboco.services.prompter_live import get_live_registry

        get_live_registry().open(session_id, SECRETARY_AGENT_ID)
        self._schedule_bg(
            self._spawn_secretary_container_guarded(
                session_id, initial_message=initial_message
            )
        )

    async def spawn_secretary_session(
        self, session_id: str, *, initial_message: str | None = None
    ) -> AgentInstance:
        """Spawn the Secretary container synchronously (internal callers/tests)."""
        from roboco.services.prompter_live import get_live_registry

        get_live_registry().open(session_id, SECRETARY_AGENT_ID)
        return await self._spawn_secretary_container(
            session_id, initial_message=initial_message
        )

    async def _spawn_secretary_container_guarded(
        self, session_id: str, *, initial_message: str | None
    ) -> None:
        """Background spawn; surface failures on the relay, not silently."""
        from roboco.services.prompter_live import get_live_registry

        try:
            await self._spawn_secretary_container(
                session_id, initial_message=initial_message
            )
        except _SpawnAbortedDuringShutdown:
            # Shutdown began mid-spawn; the just-started container was already
            # removed by the raiser. Close the relay silently — shutdown is not
            # a user-facing failure, so no error is pushed to the SSE stream.
            get_live_registry().close(session_id)
            return
        except Exception as exc:
            logger.error(
                "Secretary container spawn failed",
                session_id=session_id,
                error=str(exc),
            )
            registry = get_live_registry()
            registry.push(
                session_id,
                {"kind": "error", "text": f"Couldn't start the Secretary: {exc}"},
            )
            registry.close(session_id)

    async def _spawn_secretary_container(
        self, session_id: str, *, initial_message: str | None
    ) -> AgentInstance:
        """Launch the Secretary SDK-driver container and track the instance.

        Unlike intake there is no workspace scope to clone — the Secretary reads
        company state through the API, so its cwd is the baked ``/app`` tree. It
        gets an HMAC agent token so its directive tools authenticate as the
        Secretary role.

        Serialized by ``_secretary_spawn_lock`` for the same reason intake is
        serialized by ``_intake_spawn_lock``: a single fixed agent id, so two
        concurrent starts would race on the container name and the ``_instances``
        write. See ``_spawn_intake_container`` for the deadlock-ordering note.
        """
        async with self._secretary_spawn_lock:
            from roboco.agents_config import issue_agent_token
            from roboco.foundation.identity import AGENTS
            from roboco.models.base import ModelProvider

            if SECRETARY_AGENT_ID in self._instances:
                await self.stop_agent(
                    SECRETARY_AGENT_ID,
                    graceful=False,
                    stop_reason="secretary_respawn_guard",
                )

            prompt_path = self._generate_composed_prompt(SECRETARY_AGENT_ID)
            route = await self._resolve_agent_route(SECRETARY_AGENT_ID)
            _reject_interactive_unsupported_provider(
                SECRETARY_AGENT_ID, route.provider_type
            )
            cli_model = _resolve_agent_cli_model(
                route.provider_type.value, route.model_name
            )
            api_url = (
                "http://roboco-orchestrator:8000"
                if PROJECT_HOST_PATH
                else f"http://127.0.0.1:{settings.port}"
            )

            is_grok = route.provider_type == ModelProvider.GROK
            image = (
                GROK_SECRETARY_IMAGE if is_grok else get_agent_image(SECRETARY_AGENT_ID)
            )
            if is_grok:
                await self._ensure_grok_interactive_image(image)
                self._ensure_grok_usage_dir(SECRETARY_AGENT_ID)
            else:
                await self._ensure_agent_image(SECRETARY_AGENT_ID)
            container_name = f"roboco-agent-{SECRETARY_AGENT_ID}"
            await self._remove_container(
                container_name, stop_reason="pre_spawn_stale_clear"
            )

            agent_uuid = str(AGENTS[SECRETARY_AGENT_ID].uuid)
            cmd = self._build_secretary_run_cmd(
                _SecretaryRunSpec(
                    container_name=container_name,
                    image=image,
                    hosts=self._resolve_secretary_host_paths(),
                    session_id=session_id,
                    cwd="/app",
                    cli_model=cli_model,
                    api_url=api_url,
                    agent_uuid=agent_uuid,
                    agent_token=issue_agent_token(
                        agent_uuid,
                        "secretary",
                        get_agent_team(SECRETARY_AGENT_ID) or "",
                    ),
                    provider_base_url=route.base_url,
                    provider_auth_token=route.auth_token,
                    provider_type=route.provider_type.value,
                    model=route.model_name,
                )
            )
            # Insert before the trailing image element (docker run flags must
            # precede the image, not follow it).
            cmd[-1:-1] = await compose_label_args(SECRETARY_AGENT_ID)
            container_id = await self._run_container_cmd(cmd)

            # Shutdown may have begun while this (non-blocking) spawn was in flight
            # — see the matching guard in _spawn_intake_container. Remove the
            # just-started container and abort WITHOUT registering, so it isn't
            # orphaned by a stop() that has already iterated _instances.
            if not self._running:
                await self._remove_container(
                    container_name, stop_reason="spawn_aborted_shutdown"
                )
                raise _SpawnAbortedDuringShutdown(SECRETARY_AGENT_ID)

            config = AgentConfig(
                agent_id=SECRETARY_AGENT_ID,
                blueprint_path=prompt_path,
                model=route.model_name,
                git_context=None,
                provider_type=route.provider_type.value,
            )
            instance = AgentInstance(
                agent_id=SECRETARY_AGENT_ID,
                state=AgentState.ACTIVE,
                config=config,
                current_task_id=None,
            )
            instance.container_id = container_id
            instance.started_at = datetime.now(UTC)
            instance.last_activity = datetime.now(UTC)
            self._instances[SECRETARY_AGENT_ID] = instance

            # Pin a usage session id so the reap finalizer can attribute token
            # usage (else $0); see the matching note in _spawn_intake_container.
            usage_session_id = await self._record_spawn_session(config, None)
            if usage_session_id is not None:
                instance.usage_session_id = usage_session_id

            logger.info(
                "Secretary session spawned",
                session_id=session_id,
                container_id=container_id[:12],
            )
            self._fire_audit(
                event_type="agent.spawned",
                agent_slug=SECRETARY_AGENT_ID,
                details={"session_id": session_id},
            )
            if initial_message:
                self._schedule_intake_first_message(session_id, initial_message)
            return instance

    def _resolve_secretary_host_paths(self) -> dict[str, str | None]:
        """Host paths for the Secretary container's mounts (claude + prompt).

        No workspaces mount: the Secretary reads company state via the API and
        runs from the baked ``/app`` tree.
        """
        if PROJECT_HOST_PATH:
            return {
                "claude": CLAUDE_AUTH_HOST_PATH,
                "prompt": (
                    f"{DATA_HOST_PATH}/prompts-generated/{SECRETARY_AGENT_ID}-prompt.md"
                ),
                "grok_usage": f"{DATA_HOST_PATH}/grok-usage/{SECRETARY_AGENT_ID}",
            }
        return {
            "claude": CLAUDE_AUTH_HOST_PATH,
            "prompt": str(
                Path(tempfile.gettempdir())
                / "roboco-prompts"
                / f"{SECRETARY_AGENT_ID}-prompt.md"
            ),
            "grok_usage": str(
                Path(tempfile.gettempdir()) / "roboco-grok-usage" / SECRETARY_AGENT_ID
            ),
        }

    @staticmethod
    def _build_secretary_run_cmd(spec: _SecretaryRunSpec) -> list[str]:
        """Compose the `docker run` argv for the persistent Secretary container."""
        cmd: list[str] = [
            "docker",
            "run",
            "-d",
            "--name",
            spec.container_name,
            "--network",
            AGENT_NETWORK,
            "-v",
            f"{spec.hosts['claude']}:/home/agent/.claude",
        ]
        AgentOrchestrator._append_claude_json_mount(cmd, spec.hosts)
        cmd.extend(
            [
                "-v",
                f"{spec.hosts['prompt']}:/app/system-prompt.md:ro",
                "-e",
                f"ROBOCO_AGENT_ID={spec.agent_uuid}",
                "-e",
                "ROBOCO_AGENT_ROLE=secretary",
                "-e",
                f"ROBOCO_AGENT_TOKEN={spec.agent_token}",
                "-e",
                f"ROBOCO_API_URL={spec.api_url}",
                "-e",
                f"ROBOCO_SECRETARY_SESSION_ID={spec.session_id}",
                "-e",
                f"ROBOCO_WORKSPACE={spec.cwd}",
                "-e",
                f"CLAUDE_CODE_SUBAGENT_MODEL={spec.cli_model}",
            ]
        )
        AgentOrchestrator._append_interactive_provider_env(cmd, spec)
        cmd.append(spec.image)
        return cmd

    async def _clone_intake_scope(
        self,
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Clone the chat scope's repo(s); return (container cwd, all paths).

        ``project`` → one repo; ``product`` → each distinct cell project (the
        Main-PM-style distinct-repo set, kept in its deterministic team order so
        the primary is stable); ``project_ids`` → a MegaTask's explicit set of
        (possibly unrelated) projects, in the order given. The agent's cwd is the
        primary project's intake workspace; for a multi-repo scope the sibling
        repos sit alongside it under ``/data/workspaces`` and are readable via
        Grep/Glob/Read.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.project import get_project_service
        from roboco.services.workspace import WorkspaceService

        team = get_agent_team(INTAKE_AGENT_ID) or "board"
        factory = get_session_factory()
        async with factory() as db:
            slugs = await self._intake_scope_slugs(
                db, project_slug, product_id, project_ids
            )
            # ponytail: a multi-project scope may list several projects pointing
            # at the same repo (a monorepo's cell-projects share one git_url);
            # clone each git_url once, mirroring CI-watch's per-git_url dedupe.
            project_svc = get_project_service(db)
            slugs_with_urls: list[tuple[str, str | None]] = []
            for slug in slugs:
                project = await project_svc.get_by_slug(slug)
                slugs_with_urls.append((slug, project.git_url if project else None))
            slugs = AgentOrchestrator._dedupe_slugs_by_git_url(slugs_with_urls)
            ws = WorkspaceService(db)
            for slug in slugs:
                await ws.ensure_workspace(slug, INTAKE_AGENT_ID)
        # Container-side paths (the workspaces tree is mounted at
        # /data/workspaces inside the container, regardless of the host root).
        paths = [_agent_workspace_path(slug, team, INTAKE_AGENT_ID) for slug in slugs]
        return paths[0], paths

    @staticmethod
    def _dedupe_slugs_by_git_url(
        slugs_with_urls: list[tuple[str, str | None]],
    ) -> list[str]:
        """Keep the first slug for each non-empty git_url (a monorepo's
        cell-projects share one git_url — clone it once, mirroring CI-watch's
        per-git_url dedupe). A slug with no/empty git_url is never collapsed
        onto another, so each distinct local repo still clones. Order is
        preserved; the primary (first) slug of a shared url wins.
        """
        seen: set[str] = set()
        deduped: list[str] = []
        for slug, git_url in slugs_with_urls:
            key = (git_url or "").strip()
            if key:
                if key in seen:
                    continue
                seen.add(key)
            deduped.append(slug)
        return deduped

    @staticmethod
    async def _intake_scope_slugs(
        db: Any,
        project_slug: str | None,
        product_id: str | None,
        project_ids: list[str] | None = None,
    ) -> list[str]:
        """Resolve the chat scope to the project slug(s) to clone."""
        if project_slug:
            return [project_slug]
        if project_ids:
            return await AgentOrchestrator._slugs_for_project_ids(db, project_ids)
        if product_id:
            return await AgentOrchestrator._slugs_for_product(db, product_id)
        raise ValueError(
            "intake scope requires project_slug, product_id, or project_ids"
        )

    @staticmethod
    async def _slugs_for_project_ids(db: Any, project_ids: list[str]) -> list[str]:
        """MegaTask scope: the slugs of an explicit set of (unrelated) projects."""
        from uuid import UUID

        from roboco.services.project import get_project_service

        project_svc = get_project_service(db)
        slugs: list[str] = []
        for pid in project_ids:
            project = await project_svc.get(UUID(pid))
            # Fail loud on ANY unresolvable id (matching the single-project route's
            # 404) rather than silently cloning fewer repos — a partial scope would
            # let the agent draft against an incomplete workspace with no signal.
            if not (project and project.slug):
                raise ValueError(f"MegaTask scope: project {pid} not found")
            slugs.append(project.slug)
        if not slugs:
            raise ValueError("MegaTask scope resolves to no projects")
        return slugs

    @staticmethod
    async def _slugs_for_product(db: Any, product_id: str) -> list[str]:
        """Product scope: the distinct cell-project slugs, in deterministic order."""
        from uuid import UUID

        from roboco.services.product import ProductService
        from roboco.services.project import get_project_service

        project_ids = await ProductService(db).distinct_project_ids(UUID(product_id))
        project_svc = get_project_service(db)
        slugs: list[str] = []
        for pid in project_ids:
            project = await project_svc.get(pid)
            if project and project.slug:
                slugs.append(project.slug)
        if not slugs:
            raise ValueError(f"product {product_id} resolves to no projects")
        return slugs

    def _resolve_intake_host_paths(self) -> dict[str, str | None]:
        """Host paths for the intake container's three mounts (claude/prompt/ws).

        Mirrors ``_resolve_host_paths`` but only for what the driver needs —
        there is no settings.json, MCP config, or briefing for the intake agent.
        """
        if PROJECT_HOST_PATH:
            return {
                "claude": CLAUDE_AUTH_HOST_PATH,
                "prompt": (
                    f"{DATA_HOST_PATH}/prompts-generated/{INTAKE_AGENT_ID}-prompt.md"
                ),
                "workspaces": f"{DATA_HOST_PATH}/workspaces",
                "grok_usage": f"{DATA_HOST_PATH}/grok-usage/{INTAKE_AGENT_ID}",
            }
        return {
            "claude": CLAUDE_AUTH_HOST_PATH,
            "prompt": str(
                Path(tempfile.gettempdir())
                / "roboco-prompts"
                / f"{INTAKE_AGENT_ID}-prompt.md"
            ),
            "workspaces": str(Path(settings.workspaces_root)),
            "grok_usage": str(
                Path(tempfile.gettempdir()) / "roboco-grok-usage" / INTAKE_AGENT_ID
            ),
        }

    @staticmethod
    def _append_interactive_provider_env(
        cmd: list[str], spec: "_IntakeRunSpec | _SecretaryRunSpec"
    ) -> None:
        """Inject the per-provider LLM env for an interactive container.

        GROK runs on the official ``grok`` CLI, exactly like the one-shot path:
        the subscription auth (``~/.grok/auth.json``) is mounted read-only, no
        metered xAI key is used, the per-agent data dir is mounted so the driver's
        per-turn usage capture lands a ``usage.json`` the finalizer reads back, and
        the per-role permissions / reasoning come from the grok flags the driver
        computes (``grok_cli_config``) — not env. Every other provider uses the
        Claude path's ``ANTHROPIC_*`` injection (or the mounted ``~/.claude``
        default when the route carries no creds).
        """
        from roboco.llm.providers.grok import GrokCliProvider
        from roboco.models.base import ModelProvider

        base_url = spec.provider_base_url
        auth_token = spec.provider_auth_token
        if spec.provider_type == ModelProvider.GROK.value:
            GrokCliProvider._append_grok_auth_mount(cmd)
            GrokCliProvider._append_usage_mount(cmd, spec.hosts)
            cmd.extend(
                [
                    "-e",
                    "ROBOCO_AGENT_MODEL=grok-build",
                    "-e",
                    "ROBOCO_GROK_USAGE_FILE=/home/agent/.grok-usage/usage.json",
                ]
            )
            return
        if base_url:
            cmd.extend(["-e", f"ANTHROPIC_BASE_URL={base_url}"])
        if auth_token:
            cmd.extend(["-e", f"ANTHROPIC_AUTH_TOKEN={auth_token}"])

    @staticmethod
    def _build_intake_run_cmd(spec: _IntakeRunSpec) -> list[str]:
        """Compose the `docker run` argv for the persistent intake container.

        No claude CLI args (the image ENTRYPOINT is the SDK driver), no
        settings.json/hook mount (the driver owns port 9000), no MCP config.
        The driver reads ``/app/system-prompt.md`` and the env below.
        """
        cmd: list[str] = [
            "docker",
            "run",
            "-d",
            "--name",
            spec.container_name,
            "--network",
            AGENT_NETWORK,
            "-v",
            f"{spec.hosts['claude']}:/home/agent/.claude",
        ]
        AgentOrchestrator._append_claude_json_mount(cmd, spec.hosts)
        cmd.extend(
            [
                "-v",
                f"{spec.hosts['prompt']}:/app/system-prompt.md:ro",
                "-v",
                f"{spec.hosts['workspaces']}:/data/workspaces",
                "-e",
                f"ROBOCO_AGENT_ID={INTAKE_AGENT_ID}",
                "-e",
                f"ROBOCO_AGENT_ROLE={get_agent_role(INTAKE_AGENT_ID) or 'prompter'}",
                "-e",
                f"ROBOCO_API_URL={spec.api_url}",
                "-e",
                f"ROBOCO_PROMPTER_SESSION_ID={spec.session_id}",
                "-e",
                f"ROBOCO_WORKSPACE={spec.cwd}",
                "-e",
                f"CLAUDE_CODE_SUBAGENT_MODEL={spec.cli_model}",
            ]
        )
        # GROK mounts the subscription auth + usage dir; other providers use the
        # ANTHROPIC_* injection or the mounted ~/.claude default.
        AgentOrchestrator._append_interactive_provider_env(cmd, spec)
        cmd.append(spec.image)
        return cmd

    async def _run_container_cmd(self, cmd: list[str]) -> str:
        """Run a detached `docker run` and return the container id."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start intake container: {stderr.decode()}")
        return stdout.decode().strip()

    def _schedule_intake_first_message(
        self, session_id: str, text: str, *, persist: bool = False
    ) -> None:
        """Fire-and-forget the opening message once the container is reachable.

        ``persist`` is True only for intake sessions (durably recorded via
        ``PrompterService``, matching every later turn) — the Secretary's
        equivalent call site leaves it False since Secretary chats have no
        durable-message table wired.
        """
        self._schedule_bg(self._deliver_when_ready(session_id, text, persist=persist))

    async def _deliver_when_ready(
        self,
        session_id: str,
        text: str,
        *,
        attempts: int = 30,
        delay: float = 1.0,
        persist: bool = False,
    ) -> None:
        """Retry-deliver the first message until the container receiver is up.

        This is the single seam every intake opening turn (a fresh ``/live/start``,
        a cold or MegaTask re-interview brief, and a Telegram-bridged
        ``/newtask``) converges through — closing it here, rather than at each
        caller, is what makes the fix cover all of them. ``persist=True``
        durably records the turn in ``prompter_messages`` once delivered, so the
        first message survives a restart exactly like every later one
        (``send_message`` already does this for turn 2+).
        """
        from roboco.services.prompter_live import get_live_registry

        registry = get_live_registry()
        for _ in range(attempts):
            if await registry.deliver(session_id, text):
                if persist:
                    await self._persist_intake_first_message(session_id, text)
                return
            await asyncio.sleep(delay)
        logger.warning(
            "Intake first message never delivered (receiver never came up)",
            session_id=session_id,
        )

    async def _persist_intake_first_message(self, session_id: str, text: str) -> None:
        """Durably record a live intake session's opening human turn.

        Best-effort (mirrors ``_inject_board_brief_into_parked_intake``): a
        persistence failure is logged, never raised into the delivery path.
        """
        from roboco.db.base import get_db_context
        from roboco.services.prompter import get_prompter_service

        try:
            async with get_db_context() as db:
                await get_prompter_service(db).record_live_message(
                    session_id, "user", text
                )
        except Exception as exc:
            logger.warning(
                "Failed to persist intake opening message",
                session_id=session_id,
                error=str(exc),
            )
