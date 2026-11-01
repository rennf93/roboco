"""Background LLM client: the one path every orchestrator-side text-LLM call takes.

RAG answer synthesis and its health probe, memory distillation, vault intake
extraction, X and video drafting, standards validation, the Mentor, each
index's ``ask`` and the eval judge all generate text outside any agent. Each
used to POST straight to Ollama's OpenAI-compatible endpoint with the full
``glm-5.3:cloud`` model. On 2026-10-28 RAG synthesis failed three times with
HTTP 402 because the Ollama Cloud account had no credit, and nothing answered
in its place.

Every such call now goes through :func:`generate_background_reply`, which
follows the active AI provider (``ModelRoutingService.derive_mode``):

* ``anthropic`` mode (Claude is the main driver): Claude Haiku only, Ollama
  is never called.
* any other mode: Ollama's flash model (``settings.local_llm_model``) first,
  then Claude Haiku when Ollama cannot answer (an HTTP error, a connection
  error, a timeout, a malformed or empty reply). An HTTP 402 also skips Ollama
  for ``_OLLAMA_NO_CREDIT_COOLDOWN_SECONDS``.

Every fallback is logged with the call site's purpose, the backend, its model,
the reason and the HTTP status. When every backend fails the reply carries no
text and the call site keeps its own failure behavior.

Haiku runs through ``claude-agent-sdk`` on the Claude Code login
(``settings.background_llm_claude_home``/.claude), never a metered API key,
locked down to one turn with no tools, no MCP servers, no hooks, plugins or
settings from that directory, a scratch working directory and no session
transcript (``roboco.agent_sdk.haiku_turn.locked_down_haiku_options``). The
turn runs in a child process started as the uid:gid that own the login, with
no supplementary groups and only an allowlisted environment, so nothing the
CLI writes into the login (an OAuth refresh, a config backup, session
entries) ever lands root-owned.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import stat
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog

from roboco.agent_sdk.haiku_turn import HaikuTurnOutcome, HaikuTurnRequest
from roboco.config import settings
from roboco.db.base import get_db_context
from roboco.models.runtime import MODEL_MAP
from roboco.services.llm import get_model_routing_service

logger = structlog.get_logger()

_ERROR_DETAIL_MAX_CHARS = 300

# Output tokens a Haiku call gets on top of the call site's answer budget, for
# the adaptive thinking Haiku 5.5 always does. 2048 is twice the smallest
# thinking budget the Claude Code CLI itself grants (1024): an allowance chosen
# from that floor, not a measurement of what low-effort thinking uses.
_HAIKU_THINKING_HEADROOM_TOKENS = 2048

# The mode changes only on an operator action; the api, dispatcher and indexer
# each re-read it after at most this long.
_ROUTING_MODE_REREAD_SECONDS = 30.0

# Only a top-up ends a 402: six probes an hour per process, and Ollama is back
# within ten minutes of a top-up.
_OLLAMA_NO_CREDIT_COOLDOWN_SECONDS = 600.0


@dataclass(frozen=True)
class BackgroundPrompt:
    """One orchestrator-side text-generation request.

    ``purpose`` names the call site in every log line. ``timeout_seconds``
    bounds each backend attempt, so a call that falls back can take up to
    twice as long. ``temperature`` and ``ollama_num_ctx`` reach Ollama only:
    the Claude Code CLI exposes neither.
    """

    purpose: str
    user_prompt: str
    max_tokens: int
    timeout_seconds: float
    system_prompt: str | None = None
    temperature: float | None = None
    ollama_num_ctx: int | None = None


@dataclass(frozen=True)
class BackendFailure:
    """Why one backend gave no text for a prompt."""

    backend: str
    model: str
    reason: str
    status: int | None = None

    def describe(self) -> str:
        return f"{self.backend} ({self.model}): {self.reason}"


@dataclass(frozen=True)
class BackgroundReply:
    """The text a backend produced, or ``text=None`` when every backend failed."""

    text: str | None
    backend: str | None
    model: str | None
    failures: tuple[BackendFailure, ...] = ()

    def describe_failures(self) -> str:
        return "; ".join(failure.describe() for failure in self.failures)


class BackendUnavailableError(Exception):
    """A backend could not produce text for this prompt."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


async def read_routing_mode() -> str:
    """The active routing mode, read with a short-lived session of its own:
    most callers are fire-and-forget and hold no session."""
    async with get_db_context() as session:
        return await get_model_routing_service(session).derive_mode()


class RoutingModeCache:
    """The routing mode, re-read at most once per
    ``_ROUTING_MODE_REREAD_SECONDS``. A failed read keeps the
    last mode known (``None`` before the first successful read)."""

    def __init__(self) -> None:
        self._mode: str | None = None
        self._read_at: float | None = None

    async def current_mode(self) -> str | None:
        if self._is_fresh():
            return self._mode
        try:
            mode = await read_routing_mode()
        except Exception as exc:
            logger.warning(
                "Background LLM could not read the routing mode",
                error=f"{type(exc).__name__}: {exc}",
                last_known_mode=self._mode,
            )
            return self._mode
        self._mode, self._read_at = mode, time.monotonic()
        return mode

    def _is_fresh(self) -> bool:
        if self._read_at is None:
            return False
        age = time.monotonic() - self._read_at
        return age < _ROUTING_MODE_REREAD_SECONDS


class NoCreditCooldown:
    """The window after an Ollama HTTP 402 during which Ollama is skipped."""

    def __init__(self) -> None:
        self._ends_at = 0.0

    def start(self) -> None:
        self._ends_at = time.monotonic() + _OLLAMA_NO_CREDIT_COOLDOWN_SECONDS

    def seconds_left(self) -> float:
        return max(0.0, self._ends_at - time.monotonic())


_ROUTING_MODE_CACHE = RoutingModeCache()
_OLLAMA_NO_CREDIT_COOLDOWN = NoCreditCooldown()


def _require_text(content: object) -> str:
    if not isinstance(content, str) or not content.strip():
        raise BackendUnavailableError("empty reply")
    return content


def _chat_messages(prompt: BackgroundPrompt) -> list[dict[str, str]]:
    system = (
        []
        if prompt.system_prompt is None
        else [{"role": "system", "content": prompt.system_prompt}]
    )
    return [*system, {"role": "user", "content": prompt.user_prompt}]


def _ollama_chat_payload(prompt: BackgroundPrompt) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.local_llm_model,
        "messages": _chat_messages(prompt),
        "max_tokens": prompt.max_tokens,
    }
    if prompt.temperature is not None:
        payload["temperature"] = prompt.temperature
    if prompt.ollama_num_ctx is not None:
        payload["options"] = {"num_ctx": prompt.ollama_num_ctx}
    return payload


def _refuse_ollama_during_no_credit_cooldown() -> None:
    seconds_left = _OLLAMA_NO_CREDIT_COOLDOWN.seconds_left()
    if seconds_left > 0:
        raise BackendUnavailableError(
            f"skipped: no-credit cooldown after HTTP 402, {seconds_left:.0f}s left"
        )


async def _post_ollama_chat_completion(prompt: BackgroundPrompt) -> httpx.Response:
    url = f"{settings.local_llm_base_url}/chat/completions"
    try:
        async with (
            asyncio.timeout(prompt.timeout_seconds),
            httpx.AsyncClient(timeout=prompt.timeout_seconds) as client,
        ):
            return await client.post(url, json=_ollama_chat_payload(prompt))
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise BackendUnavailableError(
            f"timed out after {prompt.timeout_seconds:g}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise BackendUnavailableError(
            f"connection failed: {type(exc).__name__}: {exc}"
        ) from exc


def _ollama_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:_ERROR_DETAIL_MAX_CHARS]
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])[:_ERROR_DETAIL_MAX_CHARS]
    return response.text[:_ERROR_DETAIL_MAX_CHARS]


def _text_of_chat_completion(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BackendUnavailableError(f"malformed reply: {type(exc).__name__}") from exc
    return _require_text(content)


class OllamaBackend:
    """Ollama's OpenAI-compatible chat endpoint, flash model."""

    name = "ollama"

    @property
    def model(self) -> str:
        return settings.local_llm_model

    async def generate(self, prompt: BackgroundPrompt) -> str:
        _refuse_ollama_during_no_credit_cooldown()
        response = await _post_ollama_chat_completion(prompt)
        if response.status_code == HTTPStatus.PAYMENT_REQUIRED:
            _OLLAMA_NO_CREDIT_COOLDOWN.start()
        if not response.is_success:
            raise BackendUnavailableError(
                f"HTTP {response.status_code}: {_ollama_error_detail(response)}",
                status=response.status_code,
            )
        return _text_of_chat_completion(response)


# The child process that runs one Haiku turn (roboco/agent_sdk/haiku_turn.py).
# ``-I``: no user site, no PYTHON* variables, no cwd on sys.path.
_HAIKU_TURN_COMMAND: tuple[str, ...] = (
    sys.executable,
    "-I",
    "-m",
    "roboco.agent_sdk.haiku_turn",
)

# The variables the Haiku process keeps from the orchestrator's environment:
# what the CLI needs to run and reach the API. The orchestrator's secrets
# (database, Redis, encryption key, provider tokens) stay out of a process
# running as another user, and a metered ANTHROPIC_API_KEY never reaches the
# CLI, so it can only use the login.
_HAIKU_PROCESS_ENV_VARS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


@dataclass(frozen=True)
class ClaudeLoginOwner:
    """The Claude Code login a Haiku turn uses (``home/.claude``) and the
    uid:gid that own it."""

    home: Path
    uid: int
    gid: int

    @property
    def login(self) -> Path:
        return self.home / ".claude"


def _claude_login_owner() -> ClaudeLoginOwner:
    """Stat the login now: whoever owns it is who the CLI runs as."""
    home = Path(settings.background_llm_claude_home or Path.home())
    try:
        status = (home / ".claude").stat()
    except OSError as exc:
        raise BackendUnavailableError(
            f"no Claude Code login at {home / '.claude'}: {exc.strerror}"
        ) from exc
    return ClaudeLoginOwner(home=home, uid=status.st_uid, gid=status.st_gid)


def _identity_switch_for(owner: ClaudeLoginOwner) -> dict[str, Any]:
    """The ``create_subprocess_exec`` arguments that start the Haiku process as
    the login's owner, with no supplementary groups: none when this process
    already is that user."""
    effective_uid = os.geteuid()
    if effective_uid == owner.uid:
        return {}
    if effective_uid != 0:
        raise BackendUnavailableError(
            f"the Claude Code login at {owner.login} belongs to uid {owner.uid}; "
            f"this process (uid {effective_uid}) cannot run the CLI as that user"
        )
    return {"user": owner.uid, "group": owner.gid, "extra_groups": []}


def _plain_directory_status(directory: Path) -> os.stat_result:
    status = directory.lstat()
    if not stat.S_ISDIR(status.st_mode):
        raise BackendUnavailableError(f"{directory} is not a plain directory")
    return status


def _give_directory_to(directory: Path, owner: ClaudeLoginOwner) -> None:
    """chown ``directory`` to the login's owner when it is not theirs yet;
    only root may, and only root needs to."""
    status = _plain_directory_status(directory)
    if (status.st_uid, status.st_gid) == (owner.uid, owner.gid):
        return
    if os.geteuid() == 0:
        os.chown(directory, owner.uid, owner.gid, follow_symlinks=False)
    elif status.st_uid != owner.uid:
        raise BackendUnavailableError(
            f"{directory} belongs to uid {status.st_uid}, not the Claude Code "
            f"login's owner (uid {owner.uid})"
        )


def _hand_home_to_login_owner(owner: ClaudeLoginOwner) -> None:
    """The CLI writes ``$HOME/.claude.json``, so the home must be the owner's.
    Root hands it over only while it holds nothing but the login: the
    directory Docker created for the bind mount, never a real user's home."""
    status = _plain_directory_status(owner.home)
    if status.st_uid == owner.uid:
        return
    if [entry.name for entry in owner.home.iterdir()] != [".claude"]:
        raise BackendUnavailableError(
            f"{owner.home} belongs to uid {status.st_uid}, not the Claude Code "
            f"login's owner (uid {owner.uid}), and holds more than the login"
        )
    _give_directory_to(owner.home, owner)


def _scratch_directory_for(owner: ClaudeLoginOwner) -> Path:
    """One fixed, empty working directory per login owner. Fixed rather than
    one per call: the CLI keeps per-directory state in ``$HOME/.claude.json``,
    which a fresh directory per call would grow forever."""
    directory = Path(tempfile.gettempdir()) / f"roboco-background-llm-{owner.uid}"
    directory.mkdir(mode=0o700, exist_ok=True)
    _give_directory_to(directory, owner)
    return directory


def _haiku_process_environment(home: Path) -> dict[str, str]:
    kept = {
        name: os.environ[name] for name in _HAIKU_PROCESS_ENV_VARS if name in os.environ
    }
    return {**kept, "HOME": str(home)}


def _stop_process_group_still_running(process: asyncio.subprocess.Process) -> None:
    """Kill the Haiku process and the CLI it started (same process group)."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _outcome_of_haiku_process(
    returncode: int | None, stdout: bytes, stderr: bytes
) -> HaikuTurnOutcome:
    if returncode != 0:
        tail = stderr.decode(errors="replace")[-_ERROR_DETAIL_MAX_CHARS:]
        raise BackendUnavailableError(f"Haiku process exited {returncode}: {tail}")
    try:
        return HaikuTurnOutcome(**json.loads(stdout))
    except (ValueError, TypeError) as exc:
        raise BackendUnavailableError(
            f"Haiku process wrote no outcome: {type(exc).__name__}"
        ) from exc


async def _haiku_turn_in_login_owner_process(
    owner: ClaudeLoginOwner, request: HaikuTurnRequest, timeout_seconds: float
) -> HaikuTurnOutcome:
    """Run one turn in a child process started as the login's owner, in a
    process group of its own so a timeout or a cancel kills the CLI too."""
    process = await asyncio.create_subprocess_exec(
        *_HAIKU_TURN_COMMAND,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=request.cwd,
        env=_haiku_process_environment(owner.home),
        start_new_session=True,
        **_identity_switch_for(owner),
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await process.communicate(
                json.dumps(asdict(request)).encode()
            )
    except TimeoutError as exc:
        raise BackendUnavailableError(f"timed out after {timeout_seconds:g}s") from exc
    finally:
        _stop_process_group_still_running(process)
    return _outcome_of_haiku_process(process.returncode, stdout, stderr)


class ClaudeHaikuBackend:
    """Claude Haiku through the Claude Code CLI on the mounted login, run as
    the login's owner."""

    name = "claude"

    @property
    def model(self) -> str:
        return MODEL_MAP["haiku"]

    async def generate(self, prompt: BackgroundPrompt) -> str:
        owner = _claude_login_owner()
        _hand_home_to_login_owner(owner)
        request = HaikuTurnRequest(
            model=MODEL_MAP["haiku"],
            user_prompt=prompt.user_prompt,
            system_prompt=prompt.system_prompt or "",
            output_cap_tokens=prompt.max_tokens + _HAIKU_THINKING_HEADROOM_TOKENS,
            cwd=str(_scratch_directory_for(owner)),
        )
        outcome = await _haiku_turn_in_login_owner_process(
            owner, request, prompt.timeout_seconds
        )
        if outcome.error_detail is not None:
            raise BackendUnavailableError(
                outcome.error_detail, status=outcome.api_error_status
            )
        return _require_text(outcome.text)


class _TextBackend(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def generate(self, prompt: BackgroundPrompt) -> str: ...


_OLLAMA_BACKEND = OllamaBackend()
_CLAUDE_HAIKU_BACKEND = ClaudeHaikuBackend()

# The backends that answer, in order, while a routing mode is active. A
# provider that becomes a main driver gets its own cheap model as one entry
# here (doctrine: .claude/rules/agent-providers.md). Every mode not listed,
# and a mode that could not be read, tries Ollama first, then Haiku.
_BACKEND_CHAIN_BY_MODE: dict[str, tuple[_TextBackend, ...]] = {
    "anthropic": (_CLAUDE_HAIKU_BACKEND,),
}
_DEFAULT_BACKEND_CHAIN: tuple[_TextBackend, ...] = (
    _OLLAMA_BACKEND,
    _CLAUDE_HAIKU_BACKEND,
)


def _backend_chain_for(mode: str | None) -> tuple[_TextBackend, ...]:
    return _BACKEND_CHAIN_BY_MODE.get(mode or "", _DEFAULT_BACKEND_CHAIN)


def _name_of_backend_after(
    chain: tuple[_TextBackend, ...], position: int
) -> str | None:
    return chain[position + 1].name if position + 1 < len(chain) else None


def _log_backend_failure(
    prompt: BackgroundPrompt,
    failure: BackendFailure,
    mode: str | None,
    next_backend: str | None,
) -> None:
    event = (
        "Background LLM backend failed, falling back"
        if next_backend
        else "Background LLM backend failed, no backend left"
    )
    logger.warning(
        event,
        purpose=prompt.purpose,
        mode=mode,
        backend=failure.backend,
        model=failure.model,
        reason=failure.reason,
        status=failure.status,
        next_backend=next_backend,
    )


async def generate_background_reply(prompt: BackgroundPrompt) -> BackgroundReply:
    """Ask the active provider's background backends, in order, for text."""
    mode = await _ROUTING_MODE_CACHE.current_mode()
    chain = _backend_chain_for(mode)
    failures: list[BackendFailure] = []
    for position, backend in enumerate(chain):
        try:
            text = await backend.generate(prompt)
        except BackendUnavailableError as exc:
            failure = BackendFailure(
                backend.name, backend.model, exc.reason, exc.status
            )
            failures.append(failure)
            next_backend = _name_of_backend_after(chain, position)
            _log_backend_failure(prompt, failure, mode, next_backend)
            continue
        return BackgroundReply(text, backend.name, backend.model, tuple(failures))
    return BackgroundReply(None, None, None, tuple(failures))


async def generate_background_text(prompt: BackgroundPrompt) -> str | None:
    """The generated text, or ``None`` when every backend failed."""
    return (await generate_background_reply(prompt)).text
