"""Container entrypoint for the Secretary agent — the live CEO chief-of-staff.

Mirrors ``intake_main``: an in-process HTTP receiver (`POST /turn`) the
orchestrator delivers the CEO's messages to, and a relay sink that POSTs each
``StreamChunk`` to `/api/secretary/live/{session}/events`. It reuses the generic
``IntakeDriver`` + ``SdkIntakeSession`` and supplies the Secretary's SDK options
(the CEO-authority tools). ``main()`` needs the live container; the relay-sink
wiring is unit-tested.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from roboco.agent_sdk.intake_driver import IntakeDriver, SdkIntakeSession, StreamChunk
from roboco.agent_sdk.intake_main import build_receiver, make_message_source
from roboco.agent_sdk.secretary_driver import build_secretary_options

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


def make_relay_sink(
    base_url: str, session_id: str, client: httpx.AsyncClient
) -> Callable[[StreamChunk], Awaitable[None]]:
    """An ``EventSink`` that POSTs each chunk to the Secretary live relay."""
    url = f"{base_url}/api/secretary/live/{session_id}/events"

    async def _emit(chunk: StreamChunk) -> None:
        try:
            await client.post(
                url,
                json={
                    "kind": chunk.kind,
                    "text": chunk.text,
                    "tool": chunk.tool,
                    "data": chunk.data,
                },
            )
        except Exception as exc:
            logger.error(
                "Secretary relay POST failed", session_id=session_id, error=str(exc)
            )

    return _emit


async def main() -> None:  # pragma: no cover - needs the live container + SDK
    """Wire receiver + driver and run them concurrently for the chat's lifetime."""
    import uvicorn

    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        try:
            claude_json.write_text("{}", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not pre-create ~/.claude.json", error=str(exc))

    session_id = os.environ["ROBOCO_SECRETARY_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/app")
    system_prompt = Path("/app/system-prompt.md").read_text(encoding="utf-8")
    model = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or None

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    options = build_secretary_options(system_prompt=system_prompt, cwd=cwd, model=model)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[SdkIntakeSession]:
        async with SdkIntakeSession(options) as session:
            yield session

    driver = IntakeDriver(
        session_factory,
        make_message_source(queue),
        make_relay_sink(base_url, session_id, client),
    )

    bind_host = os.environ.get("ROBOCO_SDK_BIND_HOST", ".".join(["0"] * 4))
    server = uvicorn.Server(
        uvicorn.Config(
            build_receiver(queue),
            host=bind_host,
            port=_RECEIVER_PORT,
            log_level="warning",
        )
    )
    logger.info("Secretary container starting", session_id=session_id)
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
