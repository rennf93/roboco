"""
Model Catalog

Preset list of (model_name, provider_type, display_name) that the Settings
UI renders. Users never type a model name — they pick from this list, and
the router maps back to the correct pre-seeded provider row.

**Anthropic entries derive from `runtime.MODEL_MAP`** — that's the single
source of truth for which Claude versions are supported. Bumping a model
version there (e.g. `claude-opus-4-6` → `claude-opus-4-7`) updates the
catalog automatically without a second edit here.

Ollama Cloud entries are hand-maintained because Ollama's cloud tags
don't live in the rest of the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass

from roboco.models.base import ModelProvider
from roboco.models.runtime import MODEL_MAP


@dataclass(frozen=True)
class CatalogEntry:
    """One selectable model in the Settings dropdown."""

    model_name: str
    provider_type: ModelProvider
    display_name: str


# Display labels for the Anthropic short names. Order of this tuple is the
# render order in the UI dropdown; entries missing from MODEL_MAP are
# silently skipped so we don't expose a model we can't route to.
_ANTHROPIC_DISPLAY: tuple[tuple[str, str], ...] = (
    ("opus", "Claude Opus"),
    ("sonnet", "Claude Sonnet"),
    ("haiku", "Claude Haiku"),
)


def _build_anthropic_entries() -> tuple[CatalogEntry, ...]:
    """Expand MODEL_MAP into catalog entries with full version in the label.

    Keeps the UI honest: a user picking "Claude Opus" sees exactly which
    underlying Claude Code model id will be used at spawn
    (e.g. "Claude Opus · claude-opus-4-6"), so version bumps in
    `runtime.MODEL_MAP` are immediately visible.
    """
    entries: list[CatalogEntry] = []
    for short_name, label in _ANTHROPIC_DISPLAY:
        full_id = MODEL_MAP.get(short_name)
        if not full_id:
            continue
        entries.append(
            CatalogEntry(
                model_name=short_name,
                provider_type=ModelProvider.ANTHROPIC,
                display_name=f"{label} · {full_id}",
            )
        )
    return tuple(entries)


MODEL_CATALOG: tuple[CatalogEntry, ...] = (
    *_build_anthropic_entries(),
    # --- Ollama Cloud (verbatim tags) ---
    # Pro plan active as of 2026-04-22. Drop any entry that stops working —
    # the catalog is the single source of truth the Settings dropdown renders from.
    CatalogEntry("glm-5.2:cloud", ModelProvider.OLLAMA_CLOUD, "GLM 5.2"),
    CatalogEntry("kimi-k2.6:cloud", ModelProvider.OLLAMA_CLOUD, "Kimi K2.6"),
    CatalogEntry("kimi-k2.7-code:cloud", ModelProvider.OLLAMA_CLOUD, "Kimi K2.7 Code"),
    CatalogEntry("minimax-m3:cloud", ModelProvider.OLLAMA_CLOUD, "Minimax M3"),
    CatalogEntry(
        "nemotron-3-ultra:cloud", ModelProvider.OLLAMA_CLOUD, "Nemotron 3 Ultra"
    ),
    # --- Grok (xAI, OpenAI protocol) ---
    # Routes to the GROK provider → GrokCliProvider spawn (api.x.ai/v1). The xAI
    # key is set via PUT /api/providers/grok/key.
    CatalogEntry("grok-build-0.1", ModelProvider.GROK, "Grok Build 0.1"),
    # --- Codex (OpenAI, official CLI) ---
    # Routes to the OPENAI provider → CodexCliProvider spawn. Subscription auth
    # (~/.codex, from `codex login`), no metered API key — parity with Grok.
    CatalogEntry("gpt-5.3-codex", ModelProvider.OPENAI, "GPT-5.3 Codex"),
)


# Fast lookup by model_name. Enforces uniqueness at import time.
MODEL_CATALOG_BY_NAME: dict[str, CatalogEntry] = {
    e.model_name: e for e in MODEL_CATALOG
}
assert len(MODEL_CATALOG_BY_NAME) == len(MODEL_CATALOG), "duplicate model_name"


def provider_type_for_model(model_name: str) -> ModelProvider | None:
    """Return the provider type for a catalog entry, or None if unknown."""
    entry = MODEL_CATALOG_BY_NAME.get(model_name)
    return entry.provider_type if entry else None


# The Ollama model picked for "pure Ollama" mode's GLOBAL row when the
# caller doesn't override. Minimax M3 wins as the generalist because it has
# the strongest reasoning/tool-use profile and can fall back to coding/writing
# adequately if a role ends up mapped to the global default.
OLLAMA_DEFAULT_MODEL: str = "kimi-k2.7-code:cloud"
