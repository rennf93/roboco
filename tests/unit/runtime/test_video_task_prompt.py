"""Video-authoring dev prompt block: the request_render/propose_video
verification instructions appear only for ``source=VIDEO_SOURCE`` tasks,
never for anything else — mirrors test_possibilities_matrix_prompt.py's
bare-instance ``_build_dev_prompt`` idiom.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import VIDEO_SOURCE


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "title": "Video: release v1.0.0",
        "status": "claimed",
        "plan": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_video_source_prompt_includes_request_render_instruction() -> None:
    prompt = await _orch()._build_dev_prompt(_task(source=VIDEO_SOURCE))
    assert "request_render" in prompt
    assert "propose_video" in prompt
    assert "render preview" in prompt


@pytest.mark.asyncio
async def test_non_video_source_prompt_omits_request_render_instruction() -> None:
    prompt = await _orch()._build_dev_prompt(_task(source="code"))
    assert "request_render" not in prompt


@pytest.mark.asyncio
async def test_missing_source_prompt_omits_request_render_instruction() -> None:
    prompt = await _orch()._build_dev_prompt(_task())
    assert "request_render" not in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
