"""compose_prompt includes the Design bar section for frontend/ux_ui teams."""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team


def test_design_bar_present_for_frontend_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.FRONTEND, "fe-dev-1")
    assert "## Design bar" in prompt


def test_design_bar_present_for_ux_ui_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.UX_UI, "ux-dev-1")
    assert "## Design bar" in prompt


def test_design_bar_absent_for_backend_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "## Design bar" not in prompt


def test_design_bar_pointer_reaches_every_developer_without_the_content() -> None:
    """developer.md's one-line pointer (role layer, shared by every team) names
    the Design bar so FE/UX-UI devs know to look for it, but carries none of
    the actual dial/rule content — that lives only in the team layer, so a
    backend dev's prompt never carries UI-taste rules it will never need."""
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "Design bar" in prompt  # the pointer phrase itself
    assert "DESIGN_VARIANCE" not in prompt  # not the technical content


def test_design_bar_reaches_fe_qa_via_team_layer() -> None:
    """Non-dev cell roles (QA, PM, Documenter) on the frontend/ux_ui team
    inherit the full section too via the team layer — shared vocabulary for
    reviewing/scoping design work, not just for the devs implementing it."""
    prompt = compose_prompt(AgentRole.QA, Team.FRONTEND, "fe-qa")
    assert "## Design bar" in prompt


def test_niche_aesthetics_present_for_frontend_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.FRONTEND, "fe-dev-1")
    assert "## Niche aesthetic vocabularies" in prompt


def test_niche_aesthetics_present_for_ux_ui_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.UX_UI, "ux-dev-1")
    assert "## Niche aesthetic vocabularies" in prompt


def test_niche_aesthetics_absent_for_backend_team() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "## Niche aesthetic vocabularies" not in prompt


def test_image_direction_present_for_ux_ui_team_only() -> None:
    """Image-gen guidance is ux_ui-only content: frontend gets a one-line
    pointer to it (no dial/rule content), backend gets neither."""
    ux_prompt = compose_prompt(AgentRole.DEVELOPER, Team.UX_UI, "ux-dev-1")
    fe_prompt = compose_prompt(AgentRole.DEVELOPER, Team.FRONTEND, "fe-dev-1")
    be_prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "## Image direction" in ux_prompt
    assert "## Image direction" not in fe_prompt
    assert "## Image direction" not in be_prompt
    assert "Image direction" in fe_prompt  # the pointer phrase itself
    assert "Composition variety" not in fe_prompt  # not the technical content
