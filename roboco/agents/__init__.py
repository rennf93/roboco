"""Agent prompt-layer helpers.

The pre-gateway agent-class implementations (developer/qa/pm/board/... and their
factories) were removed in the gateway-cutover cleanup. Only the prompt-layer
composer survives at :mod:`roboco.agents.factories._base` (``compose_prompt``),
which the orchestrator uses to assemble per-role system prompts at spawn time.
"""
