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
    CatalogEntry("glm-5.1:cloud", ModelProvider.OLLAMA_CLOUD, "GLM 5.1"),
    CatalogEntry("kimi-k2.6:cloud", ModelProvider.OLLAMA_CLOUD, "Kimi K2.6"),
    CatalogEntry("minimax-m3:cloud", ModelProvider.OLLAMA_CLOUD, "Minimax M3"),
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


# Defaults per role when the user flips to "pure Ollama" mode.
# Assignments reflect the 2026-04 public benchmarks for each cloud tag:
#   Kimi K2.6 — HLE 44.9%, AIME 95.6%, Agent Swarm (100 parallel sub-agents),
#     200-300 sequential tool calls. Best at reasoning, orchestration, tool use.
#   MiniMax M3 — SWE-Bench 73.8%, SWE-Pro 56.2%, 10B active params (fastest,
#     cheapest). Explicitly "built for Max coding & agentic workflows".
#   GLM 5.1 — SWE-Bench 77.8% (highest of the three, 94.6% of Claude Opus 4.6),
#     self-correcting across hundreds of iterations, strong creative writing.
OLLAMA_ROLE_DEFAULTS: dict[str, str] = {
    # High-volume agentic coding — M3 is purpose-built for this.
    "developer": "minimax-m3:cloud",
    # Deep code review — GLM 5.1 has the highest SWE-Bench and iterates thoroughly.
    "qa": "glm-5.1:cloud",
    # Orchestration + tool coordination — Kimi K2.6's Agent Swarm is the exact fit.
    "cell_pm": "kimi-k2.6:cloud",
    "main_pm": "kimi-k2.6:cloud",
    # Quality reasoning — Kimi K2.6 leads HLE by a wide margin.
    "auditor": "kimi-k2.6:cloud",
    # Product reasoning — same profile as PM work.
    "product_owner": "kimi-k2.6:cloud",
    # Writing with code-context — GLM 5.1's creative writing + SWE-Bench combo.
    "documenter": "glm-5.1:cloud",
    # Stylistic writing — GLM 5.1's creative-writing strength.
    "head_marketing": "glm-5.1:cloud",
    # CEO is human-in-the-loop; keep an entry in case someone forces
    # a route to it, but the Settings UI intentionally excludes it.
    "ceo": "kimi-k2.6:cloud",
}

# The Ollama model picked for "pure Ollama" mode's GLOBAL row when the
# caller doesn't override. Minimax M3 wins as the generalist because it has
# the strongest reasoning/tool-use profile and can fall back to coding/writing
# adequately if a role ends up mapped to the global default.
OLLAMA_DEFAULT_MODEL: str = "minimax-m3:cloud"
