# RoboCo vs. Microsoft Scope

On October 19, 2026, Microsoft announced Scope, an open-source platform built on Kubernetes for evaluating AI coding agents with repeatable experiments. It is released under the MIT License. Scope drives existing coding agents, including GitHub Copilot, Claude Code, and Visual Studio Code with Copilot. The post cites one customer result: the Azure Cosmos DB team used Scope to compare bare models with its Agent Kit across eight end-to-end scenarios in five languages, and the evaluations led to 20 Agent Kit issues and 27 documentation issues resolved.

Source: [Microsoft Open Source Blog — Introducing Scope: Open-source evaluation for AI coding agents](https://opensource.microsoft.com/blog/2026/10/19/introducing-scope-open-source-evaluation-for-ai-coding-agents/)

**Response decision:** no product change is warranted. Scope evaluates how agents use a product (its SDK, docs, and tools). RoboCo's golden-task harness evaluates RoboCo's own agents on fixed tasks through its own delivery lifecycle. They answer different questions. RoboCo already ships the harness and the rework and spawn-waste metrics. It has not published a cohort result. This note records that gap and what closing it requires.

## Verified facts

Scope claims, checked against the source page:

1. **License.** The post states Scope is "available under the MIT License."
2. **Platform.** The post calls Scope "an open-source platform built on Kubernetes." It also says a local stack starts with Docker Compose, and that Scope is software you deploy, not a hosted service.
3. **Cosmos DB result.** The quote from the Azure Cosmos DB team reads: eight end-to-end scenarios, five languages, "20 Agent Kit issues and 27 documentation issues resolved." The post says the gains describe the Cosmos DB scenarios evaluated, not a general ranking of agents.
4. **Scope's own caveat.** The post says repeatable configurations do not guarantee identical agent behavior and automated judges can make mistakes. It calls for repeated runs and human review of the evidence.

RoboCo claims, checked against this checkout:

5. **Golden-task fixtures exist.** `roboco/eval/fixtures.py` defines `FIXTURES` (line 73): twelve fixtures. Six target the developer role (`bugfix-off-by-one`, `bugfix-null-check`, `small-feature-greet`, `refactor-duplicate-normalize`, `docs-readme-flag`, `research-magic-constant`; keys at lines 75, 109, 146, 176, 212, 251). Five target QA (`qa-catch-off-by-one`, `qa-catch-dropped-ac`, `qa-catch-conventions-misplacement`, `qa-catch-security-flaw`, `qa-catch-vacuous-test`; keys at lines 294, 380, 426, 478, 531). One targets the cell PM (`pm-delegate-pagination-fix`, key at line 335, `target_role="cell_pm"` at line 369). Each carries a brief, acceptance criteria, seeded repo files, and a checked-in `expectations` note (`BenchTaskSpec`, line 20).
6. **The harness replays fixtures through the real lifecycle.** `EvalRunner` (`roboco/eval/runner.py:1339`) runs each fixture as a real task in a throwaway database and a local git origin (`runner.py:26-37`). `OrchestratorStageSpawner` (`runner.py:684`) drives the real `AgentOrchestrator.spawn_agent` per turn (`runner.py:44-59`).
7. **The harness scores deterministic metrics.** `DeterministicMetrics` (`runner.py:989`) records final status, stalled flag, `revision_count`, cycle time, input/output/cache tokens, and estimated cost. `CohortResult.as_dict()` (`runner.py:1082`) emits them as JSON. The local-model judge score is nested under `"non_deterministic": True` (`runner.py:1103-1106`). Four QA fixtures name an `expected_catch_gate` (`fixtures.py:423`, `475`, `528`, `587`). `_score_catch` (`runner.py:1235`) derives caught or missed from the findings ledger and bounce audit events, never from a diff, and `as_dict()` reports it as `catch_rate` (`runner.py:1107-1111`).
8. **The harness has limits.** `run_cohort` (`runner.py:1360`) accepts the roles in `_BENCH_ROLES` (`runner.py:135`) and runs only the fixtures whose `target_role` matches the agent under test (`runner.py:1376`). The CLI is `python -m roboco.eval run --role <slug> --cohort <name> [--fixtures a,b] [--json-out path]` (`roboco/eval/__main__.py:3`). It runs from a source checkout only and needs a Docker daemon and built agent images (`runner.py:34-37`, `runner.py:57-59`, `__main__.py:10-16`).
9. **Rework rate is computed.** `MetricsService.get_rework_metrics` (`roboco/services/metrics.py:442`) returns rework rate, per-team and per-agent rates, and rework cost. A task counts as reworked when it completed in the window with `revision_count > 0` (`metrics.py:307-326`). Rework cost sums spawn-session cost for those tasks (`metrics.py:415-440`). It is served at `GET /metrics/rework` (`roboco/api/routes/dashboard.py:564-573`).
10. **Spawn waste is computed, under two definitions.** `MetricsService.get_spawn_waste_metrics` (`metrics.py:1383`) prices sessions with no forward-progress signal in their own window: no status advance, commit, progress entry, or journal entry. It is served at `GET /metrics/spawn-waste` (`dashboard.py:576-593`). A second metric, `UsageService.get_spawn_waste` (`roboco/services/usage.py:353`), flags zero output tokens on Claude sessions (`usage.py:373`, `usage.py:383`), served at `GET /spawn-waste` (`roboco/api/routes/usage.py:139-158`). Both docstrings say the definitions differ (`dashboard.py:586`, `usage.py:151`). Any published figure names which one it uses.
11. **What is not published.** No cohort result from `roboco/eval` appears in this repository's docs. No rework-rate or spawn-waste figure is published in `docs/positioning/`. This note publishes none and invents none.

## What changed in response and why

This document is the response. Nothing else changes.

Scope's blog post puts a number next to an evaluation practice. RoboCo has the machinery for the same kind of claim and has not made one. Facts 5 to 10 show the machinery. Fact 11 shows the gap. A harness with no published result is a claim nobody can check. This note makes the gap explicit so the next step is a task, not a talking point.

## What a falsifiable cohort result needs

1. **Cohort definition.** A label and a stated configuration: agent slug, provider, model, and doctrine version. `agent_spawn_sessions.doctrine_version` exists to identify the combination after the fact (`docs/map/db-migrations.md:136`).
2. **Fixed golden tasks.** The fixture set pinned to a commit, with the fixture keys listed. Today that is the twelve fixtures from fact 5, split by the role under test (six developer, five QA, one cell PM).
3. **Named metrics.** Pass rate, `revision_count`, cycle time, tokens, and cost, as emitted by `CohortResult.as_dict()`, plus `catch_rate` for a QA cohort. The judge score is reported separately and labeled non-deterministic.
4. **A reproducible run.** The exact `python -m roboco.eval run` command, the commit it ran on, and the environment it needs.
5. **Published raw output.** The unedited `--json-out` file, so a reader can recompute every aggregate.
6. **Repeated runs.** One run is one sample. Scope's own post says results vary between attempts. A published result reports more than one.

## Differentiated posture

Scope measures how agents use your product. RoboCo measures how its own fleet delivers: bounces, wasted spawns, cost. Scope is a deployable platform and RoboCo's harness is an offline CLI. RoboCo does not claim parity with Scope's Kubernetes deployment, its Portal, or its REST API.

What RoboCo can claim today is the harness, the fixtures, and two live metrics, each with a file path above. What it cannot claim is a result. That needs a cohort run published with its raw output.
