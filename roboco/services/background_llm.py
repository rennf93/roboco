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
  for ``settings.background_llm_ollama_no_credit_cooldown_seconds``.

Every fallback is logged with the call site's purpose, the backend, its model,
the reason and the HTTP status. When every backend fails the reply carries no
text and the call site keeps its own failure behavior.

Haiku runs through ``claude-agent-sdk`` on the Claude Code login mounted at
``~/.claude``, never a metered API key, locked down to one turn with no tools,
no MCP servers, no hooks, plugins or settings from that directory, a scratch
working directory and no session transcript (:func:`locked_down_haiku_options`).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import structlog

from roboco.config import settings
from roboco.db.base import get_db_context
from roboco.models.runtime import MODEL_MAP
from roboco.services.llm import get_model_routing_service

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

logger = structlog.get_logger()

# Process environment variables that would make the Claude Code CLI bill a
# metered credential instead of the mounted subscription login.
_METERED_ANTHROPIC_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

_ERROR_DETAIL_MAX_CHARS = 300

# Output tokens a Haiku call gets on top of the call site's answer budget, for
# the adaptive thinking Haiku 5.5 always does. 2048 is twice the smallest
# thinking budget the Claude Code CLI itself grants (1024): an allowance chosen
# from that floor, not a measurement of what low-effort thinking uses.
_HAIKU_THINKING_HEADROOM_TOKENS = 2048


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
    ``settings.background_llm_mode_cache_seconds``. A failed read keeps the
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
        return age < settings.background_llm_mode_cache_seconds


class NoCreditCooldown:
    """The window after an Ollama HTTP 402 during which Ollama is skipped."""

    def __init__(self) -> None:
        self._ends_at = 0.0

    def start(self) -> None:
        cooldown = settings.background_llm_ollama_no_credit_cooldown_seconds
        self._ends_at = time.monotonic() + cooldown

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


def _scratch_working_directory() -> Path:
    """One fixed, empty directory for every Haiku call. Fixed rather than one
    per call: the CLI keeps per-directory state in the container's
    ``~/.claude.json``, which a fresh directory per call would grow forever."""
    directory = Path(tempfile.gettempdir()) / "roboco-background-llm"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def locked_down_haiku_options(prompt: BackgroundPrompt) -> ClaudeAgentOptions:
    """The Claude Code options for one background Haiku call.

    One turn, no built-in tools, no MCP server from any source, no settings
    file (so no hooks, plugins or permissions from the mounted ``~/.claude``),
    and ``--safe-mode`` on top, which turns off every customization (CLAUDE.md,
    skills, installed plugins, hooks, MCP servers) while auth works normally.
    ``--no-session-persistence`` keeps the transcript out of
    ``~/.claude/projects``. ``verbatim_prompts`` delivers the prompt as written,
    so an ``@/path`` inside untrusted text (a vault note, an X mention) cannot
    make the CLI read a local file into it.

    Haiku 5.5's thinking cannot be turned off (the CLI sends adaptive thinking
    whatever it is asked), so effort is ``low``, and the output cap is the
    call's answer budget plus :data:`_HAIKU_THINKING_HEADROOM_TOKENS`: thinking
    tokens count against the cap, and a 5- or 200-token cap alone could be
    spent before the answer starts.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    output_cap = prompt.max_tokens + _HAIKU_THINKING_HEADROOM_TOKENS
    return ClaudeAgentOptions(
        model=MODEL_MAP["haiku"],
        system_prompt=prompt.system_prompt or "",
        tools=[],
        mcp_servers={},
        strict_mcp_config=True,
        setting_sources=[],
        permission_mode="dontAsk",
        max_turns=1,
        cwd=_scratch_working_directory(),
        verbatim_prompts=True,
        effort="low",
        env={
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(output_cap),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        extra_args={"safe-mode": None, "no-session-persistence": None},
    )


def _refuse_metered_anthropic_credentials() -> None:
    present = [
        name for name in _METERED_ANTHROPIC_CREDENTIAL_VARS if os.environ.get(name)
    ]
    if present:
        raise BackendUnavailableError(
            f"{', '.join(present)} is set in this process's environment; the "
            "Haiku fallback runs only on the mounted Claude Code login"
        )


async def _final_result_of_haiku_query(
    prompt: BackgroundPrompt,
) -> ResultMessage | None:
    from claude_agent_sdk import ResultMessage, query

    options = locked_down_haiku_options(prompt)
    final: ResultMessage | None = None
    try:
        async with asyncio.timeout(prompt.timeout_seconds):
            async for message in query(prompt=prompt.user_prompt, options=options):
                if isinstance(message, ResultMessage):
                    final = message
    except TimeoutError as exc:
        raise BackendUnavailableError(
            f"timed out after {prompt.timeout_seconds:g}s"
        ) from exc
    except Exception as exc:
        raise BackendUnavailableError(f"{type(exc).__name__}: {exc}") from exc
    return final


def _describe_error_result(result: ResultMessage) -> str:
    detail = "; ".join(result.errors or []) or (result.result or "")
    return f"{result.subtype}: {detail}"[:_ERROR_DETAIL_MAX_CHARS]


class ClaudeHaikuBackend:
    """Claude Haiku through the Claude Code CLI on the mounted login."""

    name = "claude"

    @property
    def model(self) -> str:
        return MODEL_MAP["haiku"]

    async def generate(self, prompt: BackgroundPrompt) -> str:
        _refuse_metered_anthropic_credentials()
        result = await _final_result_of_haiku_query(prompt)
        if result is None:
            raise BackendUnavailableError("no result message")
        if result.is_error:
            raise BackendUnavailableError(
                _describe_error_result(result), status=result.api_error_status
            )
        return _require_text(result.result)


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
