# RoboCo vs. Devin's Per-Task Model Selection (Grok 4.6 Added)

## Signal

On August 12, 2026, Cognition added xAI's Grok 4.6 to Devin Desktop and Devin CLI. The report positions Devin as a model-neutral control layer for software agents that selects a model per task across 100+ software-engineering categories, on performance and cost. Cognition's stated bet, as relayed to us: "the durable product is the agent system that can test, route and supervise competing models as their relative performance changes."

Source: [runtimewire — Cognition adds Grok 4.6 to Devin's multi-model coding platform](https://runtimewire.com/article/cognition-adds-grok-4-6-to-devin-s-multi-model-coding-platform)

Limit of this note: the article page is a client-rendered app, and its body did not load when fetched with `curl` on 2026-11-04. The signal above is the text of the market-signal task that carried this source. It was not re-read against the article. Nothing below depends on any Devin claim beyond that text.

## Response decision

Copy only. No routing behavior changes.

A funded incumbent now claims multi-model routing as its wedge. The accurate contrast is the unit of routing. Devin, as reported, picks one model per task. RoboCo routes the whole organization: each role, and each role at a given task complexity, gets its own provider and model, and a per-agent pin overrides both.

Two surfaces carry that contrast:

- README, "RoboCo routes the whole org, not one task at a time" paragraph (`README.md:61`). It cites the report and states the contrast in two sentences.
- The AI Routing settings card keeps RoboCo's side only (`panel/src/components/settings/ai-routing-card.tsx:1073-1075`). The competitor sentence was removed from the card after review (F-f1d1189f), because a dated press citation goes stale on a config surface.

No code changed beyond card copy.

## Verified facts

1. **The precedence ladder is agent pin, then role:complexity, then role, then global.** The module docstring states it: `AGENT_SLUG override > ROLE(":"complexity) override > ROLE override > GLOBAL default` (`roboco/services/llm.py:7-8`). The code that walks it is `_resolve_assignment` (`roboco/services/llm.py:1695-1722`): the `AGENT_SLUG` lookup at `:1707-1709`, the `role:complexity` lookup at `:1710-1713`, the plain role lookup at `:1714-1717`, the `GLOBAL` lookup at `:1718-1721`.
2. **Routing is resolved per agent at spawn, not per task alone.** `resolve_for_agent(agent_slug, complexity)` takes the agent's slug and an optional task complexity, derives the agent's role, and runs the ladder (`roboco/services/llm.py:1638-1656`).
3. **Task complexity enters as one rung, not as the whole mechanism.** `complexity` is the task's `estimated_complexity` in lowercase (`low`, `medium`, `high`) (`roboco/services/llm.py:1643-1644`). With `None`, behavior is the same as before cost-tiering (`:1645-1648`). The compound rung reuses the existing ROLE scope with a `role:complexity` value, so no schema change was needed (`roboco/services/llm.py:10-15`).
4. **Cost-tiered routing shipped in v0.27.0.** The release entry describes a compound `role:complexity` assignment rung in the routing precedence, so a role can ride a cheaper model at a given task complexity (`CHANGELOG.md:277`, under `## [0.27.0] - 2026-07-25`, `CHANGELOG.md:266`).
5. **The cost-tiered seed ships empty.** `_COST_TIERED_SEED` is `()` (`roboco/services/llm.py:184`). The earlier `developer:low` to haiku entry was retired on 2026-07-24 because haiku cannot reliably emit the structured lifecycle envelopes (`roboco/services/llm.py:175-183`). An operator can still set `role:complexity` overrides. None ship by default. This note therefore does not claim RoboCo cuts cost out of the box.

## What this note does not claim

- No claim that RoboCo selects models more accurately than Devin, or at lower cost. No benchmark exists in this repo for either.
- No claim about how Devin selects models beyond the text relayed under Signal.
- No claim that RoboCo routes per task on performance. RoboCo's routing is operator-configured by role, complexity, and agent.
