"""The two ponytail doctrine files load with the right content split."""

from __future__ import annotations

from roboco.agents.factories._base import _get_prompts_base_path, _load_layer

_PROMPTS = _get_prompts_base_path()


def test_full_doctrine_has_ladder_and_no_frontmatter() -> None:
    text = _load_layer(_PROMPTS / "doctrine" / "ponytail.md")
    assert text, "ponytail.md is missing or empty"
    assert "# Ponytail Doctrine" in text
    assert "## The ladder" in text
    assert "## RoboCo preamble" in text
    assert "## Intensity" in text  # dev doctrine carries the intensity table
    assert "Forces the laziest solution" not in text  # YAML frontmatter stripped


def test_ethos_doctrine_lacks_ladder_and_no_frontmatter() -> None:
    text = _load_layer(_PROMPTS / "doctrine" / "ponytail-ethos.md")
    assert text, "ponytail-ethos.md is missing or empty"
    assert "# Ponytail Doctrine (ethos)" in text
    assert "## The ladder" not in text
    assert "## Intensity" not in text  # ethos gets no dial — restrained stance
    assert "## RoboCo preamble" in text
    assert "Forces the laziest solution" not in text
