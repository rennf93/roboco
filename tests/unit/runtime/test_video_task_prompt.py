"""Video-authoring dev prompt block: the request_render/propose_video
verification instructions appear only for ``source=VIDEO_SOURCE`` tasks,
never for anything else — mirrors test_possibilities_matrix_prompt.py's
bare-instance ``_build_dev_prompt`` idiom.
"""

from __future__ import annotations

from pathlib import Path
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
async def test_video_source_prompt_states_the_animation_clock_rule() -> None:
    """The craft bar teaches the clock the runtime really uses (start at the
    nearest data-start, never data-start plus an equal delay), not the old
    "the clip scheduler drifts" diagnosis, and names the test that enforces
    it."""
    prompt = await _orch()._build_dev_prompt(_task(source=VIDEO_SOURCE))
    assert "Animation clock" in prompt
    assert "motion/compositions/animation-clock.test.js" in prompt
    assert "Clip windows are for structural layers" not in prompt
    assert "drops your tail" not in prompt


@pytest.mark.asyncio
async def test_video_source_prompt_previews_over_http_not_file_urls() -> None:
    """The author's playwright MCP refuses file:// URLs, so the prompt has the
    author serve motion/ over HTTP; it also keeps the one index.html at the
    project root."""
    prompt = await _orch()._build_dev_prompt(_task(source=VIDEO_SOURCE))
    assert "python3 -m http.server 8000 -d motion" in prompt
    assert "http://127.0.0.1:8000/compositions/<id>/vertical.html" in prompt
    assert "file://.../vertical.html" not in prompt
    assert "never add an index.html under compositions/" in prompt


@pytest.mark.asyncio
async def test_video_source_prompt_names_the_vendored_skill_that_exists() -> None:
    """The brief sends the author to the vendored HyperFrames skill, whose
    house overrides sit on top; the path it names must exist, so a re-vendor
    cannot leave the brief pointing at files that are gone."""
    prompt = await _orch()._build_dev_prompt(_task(source=VIDEO_SOURCE))
    assert "motion/skills/hyperframes/SKILL.md" in prompt
    assert (Path(__file__).parents[3] / "motion/skills/hyperframes/SKILL.md").is_file()
    assert "hyperframes-core" not in prompt


@pytest.mark.asyncio
async def test_video_source_prompt_states_the_platform_band() -> None:
    """TikTok draws its UI over a vertical outside y 168..1330 and down the
    right-hand rail, so the brief gives the band, the kit window that keeps a
    scene inside it, and the cut it is modelled on, which must exist."""
    prompt = await _orch()._build_dev_prompt(_task(source=VIDEO_SOURCE))
    assert "between y 168 and 1330" in prompt
    assert "right of x 900 below y 840" in prompt
    assert "pk-band" in prompt
    model = "compositions/release-0.28.0/vertical.html"
    assert model in prompt
    assert (Path(__file__).parents[3] / "motion" / model).is_file()


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
