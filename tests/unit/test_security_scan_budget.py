"""The WAF's per-pattern scan budget must outlast a stalled process.

guard-core measures ``detection_compiler_timeout`` in WALL time: a windowed
pattern's worker future is awaited with ``asyncio.wait_for`` on a 4-worker
process-wide pool, so every moment the scan waits for the pool or the event
loop counts against the budget. On 2026-11-01 04:53:42 an agent's ``POST
/api/v1/do/note`` was blocked because ``accept: */*`` "exceeded" the 2.0 s
budget; seven tiny tailnet headers did the same on 2026-10-28 05:36. These
pin the budget above the longest stall the log shows and reproduce the
failure mode with the scan pool kept busy.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import pytest
from guard.adapters import StarletteGuardRequest
from guard_core._utils.penetration_detection import detect_penetration_attempt
from guard_core.detection_engine.compiler import (
    _SHARED_EXECUTOR_MAX_WORKERS,
    shared_regex_executor,
)
from roboco import security
from starlette.requests import Request

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.types import Message

# orchestrator.log 2026-11-01 04:57:53.739 -> 04:58:03.181: two /health requests
# started 106 ms apart both completed 9.44 s / 9.33 s later, no log line in
# between, then the guard's Redis reads timed out: the whole process stalled.
_LONGEST_OBSERVED_STALL_SECONDS = 9.44

# A scan-pool stall longer than the old 2.0 s budget, far below the new one.
_POOL_STALL_SECONDS = 2.5

# The blocked request's headers, verbatim from the 11-01 log line.
_AGENT_NOTE_HEADERS = {
    "host": "roboco-dispatcher-green:8000",
    "accept": "*/*",
    "accept-encoding": "gzip, deflate",
    "connection": "keep-alive",
    "user-agent": "python-httpx/0.28.1",
    "x-agent-id": "00000000-0000-0000-0002-000000000002",
    "x-agent-role": "developer",
    "x-correlation-id": "283fdeeb-7454-49e1-8e9a-af26b89bf07e",
    "x-agent-team": "frontend",
    "x-agent-token": "redacted",
    "content-type": "application/json",
}


def _agent_note_request() -> StarletteGuardRequest:
    body = b'{"task_id": "d6a26717", "kind": "progress"}'
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/do/note",
        "raw_path": b"/api/v1/do/note",
        "query_string": b"",
        "headers": [
            (k.encode(), v.encode())
            for k, v in {
                **_AGENT_NOTE_HEADERS,
                "content-length": str(len(body)),
            }.items()
        ],
        "client": ("172.23.0.12", 40000),
        "server": ("roboco-dispatcher-green", 8000),
        "scheme": "http",
        "root_path": "",
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteGuardRequest(Request(scope, receive))


@contextlib.contextmanager
def _busy_scan_pool(seconds: float) -> Iterator[None]:
    """Every shared regex worker asleep for ``seconds``: what a stalled
    process looks like to a scan waiting on the pool."""
    pool = shared_regex_executor()
    sleepers = [
        pool.submit(time.sleep, seconds) for _ in range(_SHARED_EXECUTOR_MAX_WORKERS)
    ]
    try:
        yield
    finally:
        for sleeper in sleepers:
            sleeper.result()


def test_pattern_scan_budget_outlasts_the_longest_observed_stall() -> None:
    assert (
        security.security_config.detection_compiler_timeout
        > _LONGEST_OBSERVED_STALL_SECONDS
    )


def test_waf_still_fails_secure_on_a_scan_that_runs_out_of_budget() -> None:
    """The CEO's 2026-11-01 decision: a scan out of budget keeps blocking."""
    assert security.security_config.fail_secure is True


@pytest.mark.asyncio
async def test_small_header_scan_waits_out_a_stalled_scan_pool() -> None:
    config = security.build_security_config()
    config.enable_redis = False
    await detect_penetration_attempt(_agent_note_request(), config)

    with _busy_scan_pool(_POOL_STALL_SECONDS):
        started = time.monotonic()
        result = await asyncio.wait_for(
            detect_penetration_attempt(_agent_note_request(), config), timeout=30
        )
        waited = time.monotonic() - started

    assert waited >= _POOL_STALL_SECONDS - 0.5
    assert result.is_threat is False, result.trigger_info
