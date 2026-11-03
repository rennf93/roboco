# Effective CLI Models on `GET /api/providers`

## Overview

`GET /api/providers` (the existing routing-mode read, `roboco/api/routes/provider.py`) returns a `ModeResponse`. The response now also carries the **effective** model each subscription CLI runtime will use, plus the Ollama default model. The panel's AI routing card reads these instead of hardcoding `gpt-5.3-codex`, `gemini-2.5-pro` and `kimi-code/k3`.

No new endpoint and no route change: the fields are added to the schema only (`roboco/api/schemas/provider.py`). The change is additive, so existing consumers of `ModeResponse` keep working.

## New response fields

All four are plain strings.

| Field | Source | Default |
|---|---|---|
| `codex_cli_model` | `settings.codex_cli_model` (`ROBOCO_CODEX_CLI_MODEL`) | `gpt-5.3-codex` |
| `gemini_cli_model` | `settings.gemini_cli_model` (`ROBOCO_GEMINI_CLI_MODEL`) | `gemini-2.5-pro` |
| `kimi_cli_model` | `settings.kimi_cli_model` (`ROBOCO_KIMI_CLI_MODEL`) | `kimi-code/k3` |
| `ollama_default_model` | `OLLAMA_DEFAULT_MODEL` in `llm_catalog.py` | the catalog's Ollama default |

The three CLI fields are `Field(default_factory=lambda: settings.<name>)`, so the setting is read each time a response is built. An operator override, or a test that monkeypatches the setting, is visible on the next request without a restart of the route layer. `ollama_default_model` is a catalog constant, not an operator setting.

## Example

```json
{
  "mode": "...",
  "assignments": [],
  "codex_cli_model": "gpt-5.3-codex",
  "gemini_cli_model": "gemini-2.5-pro",
  "kimi_cli_model": "kimi-code/k3",
  "ollama_default_model": "<OLLAMA_DEFAULT_MODEL>"
}
```

(`mode` and `assignments` are unchanged and shown abbreviated.)

## Why `ModeResponse` and not `/providers/catalog`

`GET /providers/catalog` returns a bare list, so adding fields would break every list consumer. `ModeResponse` is already an object, which makes the addition backward compatible.

## Tests

- `tests/unit/api/test_mode_response_cli_models.py`: monkeypatches each CLI-model setting and asserts the schema value changes.
- `tests/integration/test_provider_routes.py::test_get_current_mode_returns_overridden_cli_models`: overrides the codex/gemini/kimi settings, calls `GET /api/providers` as a PM, and asserts the JSON values changed and `ollama_default_model == OLLAMA_DEFAULT_MODEL`. The developer could not run it locally (no Docker); CI exercises it.

## Frontend note

`panel/src/components/.../ai-routing-card.tsx` still carries a TODO and the hardcoded model names; the frontend cell should switch it to these four fields.
