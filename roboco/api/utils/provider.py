"""
Provider Route Helpers

Route-glue helpers backing roboco/api/routes/provider.py.
"""

from roboco.api.schemas.provider import ComplexityOverrideResponse
from roboco.models.base import ModelProvider

# Human remediation hint per provider type, for a complexity override that
# resolves to a not-ready (disabled / unconfigured) provider.
_PROVIDER_REMEDIATION: dict[ModelProvider, str] = {
    ModelProvider.GROK: "Save the Grok (xAI) API key first (PUT /providers/grok-key).",
    ModelProvider.OLLAMA_CLOUD: (
        "Save an Ollama Cloud API key first (PUT /providers/ollama-key)."
    ),
    ModelProvider.LOCAL: (
        "Configure + test the self-hosted server first (PUT /providers/self-hosted)."
    ),
    ModelProvider.ANTHROPIC: "The Anthropic provider is disabled — re-enable it first.",
    ModelProvider.OPENAI: (
        "Codex authenticates via a mounted ChatGPT-subscription ~/.codex "
        "directory, not a key — enable it via the Codex mode button, or "
        "assign a Codex model to an agent in Mix mode (both force-enable "
        "the row)."
    ),
    ModelProvider.GEMINI: (
        "Gemini authenticates via a mounted OAuth ~/.gemini credential, not "
        "a key — enable it via the Gemini mode button, or assign a Gemini "
        "model to an agent in Mix mode (both force-enable the row)."
    ),
    ModelProvider.KIMI: (
        "Kimi authenticates via a shared, symlinked-in ~/.kimi-code "
        "subscription credential, not a key — enable it via the Kimi mode "
        "button, or assign a Kimi model to an agent in Mix mode (both "
        "force-enable the row)."
    ),
}


def provider_remediation(provider_type: ModelProvider) -> str:
    return _PROVIDER_REMEDIATION.get(
        provider_type, f"The {provider_type.value} provider is not configured."
    )


def parse_complexity_override(
    scope_value: str, model_name: str
) -> ComplexityOverrideResponse | None:
    """Parse a ROLE scope_value into a response row, or None if not a
    well-formed "role:low"/"role:high" compound key (a plain role row, or a
    malformed compound value, are both silently skipped)."""
    role, sep, complexity = scope_value.partition(":")
    if not sep or not role:
        return None
    if complexity == "low":
        return ComplexityOverrideResponse(
            role=role, complexity="low", model_name=model_name
        )
    if complexity == "high":
        return ComplexityOverrideResponse(
            role=role, complexity="high", model_name=model_name
        )
    return None
