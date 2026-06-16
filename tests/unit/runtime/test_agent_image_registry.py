"""Agent image resolution — local build vs. pre-built registry images.

``_qualify_agent_image`` decides what image name the orchestrator spawns (and
ensures). Empty registry/tag MUST return the bare name unchanged so the local
build flow and existing NAS deployment are untouched; a configured registry
switches every agent to the pre-built ``{registry}/roboco-agent-*[:tag]``.
"""

from __future__ import annotations

import pytest
from roboco.runtime import orchestrator as orch


@pytest.mark.parametrize(
    ("registry", "tag", "bare", "expected"),
    [
        # Default: no registry, no tag -> bare name unchanged (local build).
        ("", "", "roboco-agent-pm", "roboco-agent-pm"),
        # Registry only -> qualified, implicit :latest.
        ("ghcr.io/rennf93", "", "roboco-agent-pm", "ghcr.io/rennf93/roboco-agent-pm"),
        # Trailing slash on the registry is tolerated.
        (
            "ghcr.io/rennf93/",
            "latest",
            "roboco-agent-pm",
            "ghcr.io/rennf93/roboco-agent-pm:latest",
        ),
        # Docker Hub namespace + pinned version.
        (
            "docker.io/renzof93",
            "0.5.0",
            "roboco-agent-base",
            "docker.io/renzof93/roboco-agent-base:0.5.0",
        ),
        # Tag without registry is still applied (edge case, valid).
        ("", "latest", "roboco-agent-pm", "roboco-agent-pm:latest"),
    ],
)
def test_qualify_agent_image(
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    tag: str,
    bare: str,
    expected: str,
) -> None:
    monkeypatch.setattr(orch.settings, "agent_image_registry", registry)
    monkeypatch.setattr(orch.settings, "agent_image_tag", tag)
    assert orch._qualify_agent_image(bare) == expected


def test_get_agent_image_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch.settings, "agent_image_registry", "")
    monkeypatch.setattr(orch.settings, "agent_image_tag", "")
    assert orch.get_agent_image("be-dev-1") == "roboco-agent-dev-be"
    # The PR reviewer has its own image (parity with the other agents).
    assert orch.get_agent_image("pr-reviewer-1") == "roboco-agent-pr-reviewer"
    # A genuinely unknown agent id falls back to the base image.
    assert orch.get_agent_image("nope-not-real") == "roboco-agent-base"


def test_get_agent_image_registry_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch.settings, "agent_image_registry", "ghcr.io/rennf93")
    monkeypatch.setattr(orch.settings, "agent_image_tag", "0.5.0")
    assert (
        orch.get_agent_image("be-dev-1")
        == "ghcr.io/rennf93/roboco-agent-dev-be:0.5.0"
    )
    assert (
        orch.get_agent_image("pr-reviewer-1")
        == "ghcr.io/rennf93/roboco-agent-pr-reviewer:0.5.0"
    )
