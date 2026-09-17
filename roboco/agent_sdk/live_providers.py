"""Per-provider argv builders + JSONL dialect notes for live CLI chats.

Each entry turns one conversation turn into the provider's headless argv.
The system prompt is NOT passed here - it reaches the CLI through the config
its delivery entrypoint rendered (role blueprint), matching the one-shot
path. Tools reach the CLI the same way (rendered mcpServers for the MCP
CLIs; the rendered pi extension for hummin - see hummin_live_config).

Output dialects (parsed tolerantly by HeadlessCliTurnSession._collect_text):

  * hummin    ``--mode json``      NDJSON ``message_end`` events, assistant
                                   ``message.content[].text`` blocks.
  * codex     ``exec --json``      JSONL; final answer in an
                                   ``item.completed`` event whose ``item``
                                   is an ``agent_message`` (``item.text``).
  * gemini    ``stream-json``      one ``{response, stats}`` payload; the
                                   answer is the ``response`` string.
  * kimi      ``stream-json``      JSONL; assistant text surfaces under the
                                   tolerant shapes below.
  * opencode  ``--format json``    (openrouter + nebius both run opencode)
                                   JSONL; text parts carry ``part.text``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

HUMMIN_OUTPUT_MODE = "json"
CODEX_OUTPUT_MODE = "json"
GEMINI_OUTPUT_MODE = "json"
KIMI_OUTPUT_MODE = "json"
OPENCODE_OUTPUT_MODE = "json"

# Providers that run the generic live chat driver (roboco.agent_sdk.live_main).
# Keys are ModelProvider VALUES (what routes carry): codex is powered by the
# "openai" provider, the two opencode dialects by "openrouter"/"nebius".
LIVE_CLI_PROVIDERS: frozenset[str] = frozenset(
    {"hummin", "openai", "gemini", "kimi", "openrouter", "nebius"}
)

# Per provider value: the live image and the Dockerfile that builds it (the
# CLI naming rides the delivery images: the openai value runs the codex CLI).
LIVE_INTERACTIVE_IMAGE: dict[str, str] = {
    "hummin": "roboco-agent-hummin-live",
    "openai": "roboco-agent-codex-live",
    "gemini": "roboco-agent-gemini-live",
    "kimi": "roboco-agent-kimi-live",
    "openrouter": "roboco-agent-openrouter-live",
    "nebius": "roboco-agent-nebius-live",
}

LIVE_INTERACTIVE_DOCKERFILE: dict[str, str] = {
    "hummin": "agent-hummin-live.Dockerfile",
    "openai": "agent-codex-live.Dockerfile",
    "gemini": "agent-gemini-live.Dockerfile",
    "kimi": "agent-kimi-live.Dockerfile",
    "openrouter": "agent-openrouter-live.Dockerfile",
    "nebius": "agent-nebius-live.Dockerfile",
}


def _model_env_default(fallback: str) -> str:
    return os.environ.get("ROBOCO_AGENT_MODEL", "").strip() or fallback


def _hummin_argv(prompt: str) -> list[str]:
    model = _model_env_default("glm-5.3-flash:high")
    # Extensions stay ENABLED (no --no-extensions): the rendered pi extension
    # is the Secretary/Intake tool bridge (hummin has no MCP client). No
    # --tools either: the strict allowlist only names built-ins (read/edit/
    # write/bash - there is no grep/glob) and passing one would exclude the
    # extension tools. The role blueprint reaches hummin via its rendered
    # SYSTEM.md (the render step), so no --system-prompt-override here.
    return [
        "hummin",
        "--mode",
        "json",
        "--model",
        f"zai/{model}",
        "-p",
        prompt,
    ]


def _codex_argv(prompt: str) -> list[str]:
    return [
        "codex",
        "exec",
        prompt,
        "-m",
        _model_env_default("gpt-5.3-codex"),
        "--json",
        "--skip-git-repo-check",
    ]


def _gemini_argv(prompt: str) -> list[str]:
    return [
        "gemini",
        "-p",
        prompt,
        "-m",
        _model_env_default("gemini-2.5-pro"),
        "--output-format",
        "stream-json",
    ]


def _kimi_argv(prompt: str) -> list[str]:
    return [
        "kimi",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "-m",
        _model_env_default("kimi-code/k3"),
    ]


def _opencode_argv(prompt: str) -> list[str]:
    # The opencode model REF carries the provider prefix (openrouter/...,
    # nebius/...); the delivery entrypoints pass ROBOCO_AGENT_MODEL through
    # opencode_model_ref, so the rendered config's agent model is already the
    # full ref and the CLI flag here only pins it explicitly.
    model = _model_env_default("")
    argv = ["opencode", "run", prompt, "--agent", "roboco"]
    if model:
        argv += ["--model", model]
    argv += ["--auto", "--format", "json"]
    return argv


if TYPE_CHECKING:
    from collections.abc import Callable

LIVE_ARGV_BUILDERS: dict[str, Callable[[str], list[str]]] = {
    "hummin": _hummin_argv,
    "openai": _codex_argv,
    "gemini": _gemini_argv,
    "kimi": _kimi_argv,
    "openrouter": _opencode_argv,
    "nebius": _opencode_argv,
}


def get_live_argv_builder(provider: str) -> Callable[[str], list[str]]:
    """The argv builder for one live-chat provider (raises KeyError unknown)."""
    return LIVE_ARGV_BUILDERS[provider]


# ---------------------------------------------------------------------------
# Tolerant text extraction: one accumulator fed every stdout line; the reply
# is whatever structured text the dialect surfaced, else the raw stdout.
# ---------------------------------------------------------------------------


class LiveReplyAccumulator:
    """Feeds stdout lines, surfacing the provider's final answer text."""

    def __init__(self) -> None:
        self._structured: list[str] = []
        self._raw: list[str] = []

    def feed(self, line: str) -> str | None:
        """Consume one stdout line; return an answer delta if the dialect
        carries one (None = buffered/ignored)."""
        self._raw.append(line)
        event = self._parse(line)
        if event is None:
            return None
        text = self._text_from_event(event)
        if text:
            self._structured.append(text)
            return text
        return None

    def reply(self) -> str:
        """The accumulated answer: structured text if the dialect surfaced
        any, else the raw stdout stripped (plain-text dialects)."""
        if self._structured:
            return "".join(self._structured)
        return "\n".join(self._raw).strip()

    @staticmethod
    def _parse(line: str) -> dict[str, Any] | None:
        import json

        stripped = line.strip()
        if not stripped.startswith("{"):
            return None
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    @staticmethod
    def _blocks_text(blocks: Any) -> str:
        """Concatenated text of a content-block list ('' when not one)."""
        if not isinstance(blocks, list):
            return ""
        return "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )

    @classmethod
    def _text_from_event(cls, event: dict[str, Any]) -> str:
        """Extract answer text from one dialect's event ('' when none)."""
        for extractor in (
            cls._hummin_text,
            cls._codex_text,
            cls._gemini_text,
            cls._opencode_text,
            cls._kimi_text,
        ):
            text = extractor(event)
            if text:
                return text
        return ""

    @staticmethod
    def _hummin_text(event: dict[str, Any]) -> str:
        """hummin: {"type":"message_end","message":{"role":"assistant",
        "content":[{"type":"text","text":...}]}}"""
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            return LiveReplyAccumulator._blocks_text(message.get("content"))
        return ""

    @staticmethod
    def _codex_text(event: dict[str, Any]) -> str:
        """codex: {"type":"item.completed","item":{"type":"agent_message",
        "text":...}}"""
        if event.get("type") != "item.completed":
            return ""
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            return text if isinstance(text, str) else ""
        return ""

    @staticmethod
    def _gemini_text(event: dict[str, Any]) -> str:
        """gemini: {"response": "..."} (final payload; stats ride alongside)."""
        response = event.get("response")
        return response if isinstance(response, str) else ""

    @staticmethod
    def _opencode_text(event: dict[str, Any]) -> str:
        """opencode: {"type":"...","part":{"type":"text","text":...}}"""
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            return text if isinstance(text, str) else ""
        return ""

    @staticmethod
    def _kimi_text(event: dict[str, Any]) -> str:
        """kimi / generic assistant shapes."""
        if event.get("type") != "assistant":
            return ""
        return LiveReplyAccumulator._blocks_text(event.get("content"))
