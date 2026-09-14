"""ZAI provider wiring: catalog membership, mode mapping, enum value.

The ZAI provider (Z.ai GLM family) rides the built-in Claude Code spawn
via ANTHROPIC_BASE_URL injection, so its wiring surface is intentionally
small: static catalog entries for the Mix picker, a derive_mode label,
and the rate-limit marker row. These tests pin that surface so a
refactor cannot silently drop ZAI from the picker or the mode map.
"""

from __future__ import annotations

from roboco.models.base import ModelProvider
from roboco.models.llm_catalog import provider_type_for_model
from roboco.services.llm import _SINGLE_GLOBAL_MODE_BY_PROVIDER


def test_zai_enum_value() -> None:
    assert ModelProvider.ZAI.value == "zai"
    assert ModelProvider("zai") is ModelProvider.ZAI


def test_zai_catalog_membership() -> None:
    assert provider_type_for_model("glm-5.3") is ModelProvider.ZAI
    assert provider_type_for_model("glm-5.3-flash") is ModelProvider.ZAI
    # Neighbouring GLM tags belong to other providers (the Ollama cloud
    # tags carry the :cloud suffix; ZAI ids do not).
    assert provider_type_for_model("glm-5.3:cloud") is not ModelProvider.ZAI


def test_zai_mode_mapping() -> None:
    assert _SINGLE_GLOBAL_MODE_BY_PROVIDER[ModelProvider.ZAI] == "zai"
