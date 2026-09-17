"""ZAI + HUMMIN provider wiring: catalog membership, mode mapping, enum values.

The ZAI provider (Z.ai GLM family) rides the built-in Claude Code spawn
via ANTHROPIC_BASE_URL injection; HUMMIN (the GLM go-to since 2026-09-17)
runs the GLM-native hummin CLI with the Z.ai key injected as ZAI_API_KEY.
The GLM catalog entries re-pointed from ZAI to HUMMIN (ZAI keeps its row +
key endpoint as a manual fallback but has no static-catalog entries left).
These tests pin that surface so a refactor cannot silently drop either
provider from the picker or the mode map.
"""

from __future__ import annotations

from roboco.models.base import ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG_BY_NAME, provider_type_for_model
from roboco.services.llm import _SINGLE_GLOBAL_MODE_BY_PROVIDER


def test_zai_enum_value() -> None:
    assert ModelProvider.ZAI.value == "zai"
    assert ModelProvider("zai") is ModelProvider.ZAI


def test_hummin_enum_value() -> None:
    assert ModelProvider.HUMMIN.value == "hummin"
    assert ModelProvider("hummin") is ModelProvider.HUMMIN


def test_glm_catalog_membership_is_hummin() -> None:
    assert provider_type_for_model("glm-5.3") is ModelProvider.HUMMIN
    assert provider_type_for_model("glm-5.3-flash") is ModelProvider.HUMMIN
    assert provider_type_for_model("glm-5.3-highspeed") is ModelProvider.HUMMIN
    # Neighbouring GLM tags belong to other providers (the Ollama cloud
    # tags carry the :cloud suffix; hummin ids do not).
    assert provider_type_for_model("glm-5.3:cloud") is not ModelProvider.HUMMIN


def test_zai_has_no_static_catalog_entries_left() -> None:
    # The 2026-09-17 re-point moved every GLM entry to HUMMIN; ZAI stays
    # enabled as a manual fallback (row + key endpoint), reachable only via
    # a hand-written assignment against its seeded row.
    zai_entries = [
        name
        for name, entry in MODEL_CATALOG_BY_NAME.items()
        if entry.provider_type is ModelProvider.ZAI
    ]
    assert zai_entries == []


def test_zai_and_hummin_mode_mapping() -> None:
    assert _SINGLE_GLOBAL_MODE_BY_PROVIDER[ModelProvider.ZAI] == "zai"
    assert _SINGLE_GLOBAL_MODE_BY_PROVIDER[ModelProvider.HUMMIN] == "hummin"
