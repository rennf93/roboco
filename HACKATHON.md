# RoboCo: Nebius x NVIDIA Hackathon Submission Notes

RoboCo is an AI agentic company: a virtual organization of 26 AI agents plus 1 human CEO that operates as a complete software development workforce, with a formal org chart (Board, Main PM, four delivery cells), a task lifecycle with PR gates and QA review, and a Next.js control panel for the CEO. The project predates this hackathon's submission window (its first releases shipped in July 2026: v0.26.0 on 2026-07-20, and v0.29.0 on 2026-08-21), so this document isolates exactly what was significantly updated during the submission period (August 26 to October 30, 2026) for the Stage One review.

## TL;DR

- The headline integration built for this hackathon (2026-09-13/14) is **Nebius Token Factory as a first-class LLM provider** for the entire agent fleet, with **NVIDIA's open-source Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`) as the fleet-wide default model by construction**, not as an option the operator has to find.
- A major platform release, **v0.30.0 (2026-09-13)**, landed inside the window: 16 merged PRs covering a new live-catalog OpenRouter provider, a three-process deployment split of the backend, decomposition of the orchestrator into engine mixins, real WAF scanning of the agent gateway, a dedicated embedding/indexer worker, and more.
- A post-release correctness pass (2026-09-14) fixed API-contract defects in the panel's provider settings UI.

## Track entry: Coding and Agentic Engineering

RoboCo enters the **Coding and Agentic Engineering Track**: "build coding agents and developer tools: agents that write, run, and test code in Token Factory Sandboxes." That description is RoboCo's core loop verbatim: delivery cells of developer, QA, and documenter agents write code, run test suites, and open pull requests against real git forges (GitHub, Gitea, GitLab) under a task lifecycle with QA review, PR review gates, and revision findings, coordinated by PM agents and governed from the CEO's panel. The fleet satisfies the hackathon's baseline ("a runtime call to the Token Factory inference API") through the Nemotron-routed Nebius mode described above.

**Token Factory Sandboxes execute the QA test run (the track's signature product).** The "run and test code" leg of the agent loop runs on Nebius's Sandboxes service: the `run_sandbox_tests` gateway verb (dev + QA tool surface, default-off feature flag) archives the committed workspace HEAD server-side, uploads it to the Sandboxes API, and spawns a disposable microVM that extracts the archive and runs the test command at the workspace root, then reports `exit_code`/`stdout`/`stderr` plus metered cost back through the verb's evidence envelope and a task journal entry. The microVM boots on demand and is destroyed after the run; when the flag is off or the service is unreachable, QA keeps its container shell, so the local path is never broken by the integration. Internals: `roboco/llm/providers/nebius_sandboxes.py` (REST client), `run_sandbox_tests` in `roboco/services/gateway/content_actions.py`, config under `ROBOCO_TOKEN_FACTORY_SANDBOXES_*`; see `docs/backend/api/token-factory-sandboxes.md`.

**Model mix across the Nemotron family.** The hackathon's model guidance (Nemotron 3 Ultra for serious reasoning, Nano or Super for fast, everyday calls) maps directly onto RoboCo's cost-tiered complexity routing: the fleet-wide default carries the everyday workload (Super), while the complexity-override table pins cheaper Nemotron tiers for LOW-complexity tasks and the heavier tier for HIGH-complexity or coordinator work, with a per-agent Mix mode for exceptions. Tier ids are picked from the live Token Factory catalog in the Settings picker, so the profile follows Nebius's catalog naming without code changes.

## What was significantly updated during August 26 to October 30, 2026

### 1. Nebius Token Factory becomes a first-class provider (the hackathon integration)

Before this work, RoboCo's pluggable provider layer (`roboco/llm/providers/`, keyed by `ModelProvider` in a provider registry) covered Anthropic (default), Grok, Codex, Gemini, Kimi, Ollama Cloud, self-hosted OpenAI-compatible endpoints, and (as of v0.30.0, also in-window) OpenRouter. The Nebius integration clones the full provider pattern end to end and lands on the `feature/hackathon/nebius-provider` branch:

- **Provider core and runtime**: the provider quartet `roboco/llm/providers/nebius.py` + `nebius_cli_config.py` / `_cli_sniff.py` / `_cli_usage.py` (the Ollama shape: a static Fernet-stored key injected as `NEBIUS_API_KEY`/`NEBIUS_BASE_URL` env at spawn, no auth mount, no refresh loop, initial prompt as an env var and never an argv token).
- **Agent image**: `docker/agent-nebius.Dockerfile` + `docker/scripts/nebius-agent-entrypoint.sh`, an `roboco-agent-nebius` image running agents on the opencode CLI against `https://api.tokenfactory.nebius.com/v1`, with the prompt-injection guard, an exit-78 auth preflight, and exit-75/78 rate-limit/auth park sniffing so a throttled agent parks and retries instead of dying.
- **Database**: migrations `097_modelprovider_nebius` (enum value added inside an `autocommit_block`) and `098_seed_nebius_provider` (a key-gated, `enabled=false` provider seed).
- **Routing service**: `roboco/services/llm.py` gained `set_nebius_api_key`, `apply_mode("nebius")`/`_apply_nebius`, `derive_mode` support, `probe_nebius_models`, NEBIUS in the interactive-session guard and the upsert auto-enable tuple, plus rate-limit/auth park handlers and active-token/usage sweep readers that meter every agent's Token Factory spend.
- **API**: `GET/PUT /api/providers/nebius-key`, `GET /api/providers/nebius/models` (live catalog search proxy), and `"nebius"` in the routing-mode request/response literals.
- **Panel**: a `NebiusProviderKeyRow` key card, a key-gated Nebius mode button, a searchable Nebius model picker, and the `useNebiusKey`/`useSetNebiusKey`/`useSearchNebiusModels` hooks.
- **Tests**: the OpenRouter test suites mirrored for Nebius (`tests/`, provider core + routing + API).
- **Docs**: `docs/backend/api/nebius-provider.md`, `docs/frontend/nebius-provider.md`, `docs/frontend/components/nebius-routing-ui.md`.

Two deliberate differences from the OpenRouter pattern: Token Factory's model list carries no capability metadata (so the search proxy has no tools-support filter, and `id` is the only substantive field), and there are no attribution-header settings (Nebius has no referer/title dashboard to feed).

### 2. Workloads route through NVIDIA Nemotron, not just through a Nebius URL

The submission requirement this is built around: run on Nebius and use an NVIDIA open-source model. A generic OpenAI-compatible adapter with Nemotron merely selectable would not meet it, so the integration pins Nemotron as the routed workload at every layer of the stack, and each layer carries a test asserting it:

- **Routing state**: applying Nebius mode upserts a GLOBAL assignment for `nvidia/nemotron-3-super-120b-a12b` (the `nebius_cli_model` setting, `roboco/config.py`; resolved in `_apply_nebius`, `roboco/services/llm.py`, as `default_model or settings.nebius_cli_model`). Every agent inherits the Nemotron model id from the routing store itself, even if the operator never opens the picker. Pinned by `tests/integration/test_llm_routing.py` (the stored assignment's model_name) and `tests/integration/test_provider_routes.py` (the apply-mode API response carries the same id).
- **Spawn payload**: the orchestrator launches each Nebius agent with `ROBOCO_AGENT_MODEL=nebius/nvidia/nemotron-3-super-120b-a12b` (asserted in `tests/unit/llm/providers/test_nebius_provider.py`), and the agent entrypoint falls back to the same ref if the variable were ever empty (`docker/scripts/nebius-agent-entrypoint.sh` passes `--model "${ROBOCO_AGENT_MODEL:-nebius/nvidia/nemotron-3-super-120b-a12b}"` to opencode). The vendor-prefixed ref is load-bearing: without the `nebius/` prefix opencode resolves the bare id against its built-in Anthropic provider and can never authenticate, so the prefix is what forces the call through the Token Factory provider block (asserted in `tests/unit/llm/providers/test_nebius_cli_config.py`).
- **Execution**: the `roboco-agent-nebius` image runs the workload on the opencode CLI, whose `nebius` provider block (`@ai-sdk/openai-compatible`) points at `https://api.tokenfactory.nebius.com/v1` with the Nemotron model ref.
- **Verification surface**: the entrypoint's usage capture records the exact model with each run (`usage.json`'s `model` field, asserted in `tests/unit/llm/providers/test_nebius_cli_usage.py`), the orchestrator's finalize stamps it onto the agent's spawn session (`tests/unit/runtime/test_nebius_usage_finalize.py`), and the usage API exposes it per session row and aggregates spend per model (`roboco/services/usage.py`, `get_by_model`). After any task, the panel's Usage view shows the workload executed on `nvidia/nemotron-3-super-120b-a12b`.

The picker (Settings -> AI Routing -> Nebius default model) can still narrow or change the choice within the Nemotron family and the rest of the catalog, but operator inaction always lands on Nemotron.

### 3. v0.30.0: the largest platform release in the window (2026-09-13)

Shipped inside the submission period, this release carried the in-window work of 16 merged PRs:

- **OpenRouter as a first-class, live-catalog LLM provider** (opencode runtime, live model search, per-agent routing), the direct predecessor pattern the Nebius integration extends.
- **`ROBOCO_ROLE` process split**: the single backend process became three (`api` / `dispatcher` / `indexer`) with nginx routing fleet-touching paths to the dispatcher, after a measured production incident where one GIL-bound process starved every gateway verb.
- **Orchestrator decomposition**: a ~19,700-line `AgentOrchestrator` class mechanically split into per-family engine mixins under `roboco/runtime/engines/`, with zero behavior change.
- **Real WAF scanning of the agent gateway**: all 102 gateway endpoints moved from a blanket internal whitelist to a route-level IP allowlist so the WAF actually scans agent traffic, with ban-safety rework so one false positive can never wedge the agent mesh; adopted fastapi-guard 8.0.0 / guard-core 4.0.1.
- **Dedicated embedding/indexer worker**: RAG index writes flow through a Redis stream to an `indexer` process with backlog shedding and dead-lettering; HNSW vector indexes replaced ivfflat; `pg_stat_statements` and `py-spy` shipped for live diagnosis.
- **Gateway verb `cancel_leaf`**: PMs get a mechanical, fail-closed path to close zero-diff leaves instead of oscillating in unblock loops.
- **CEO-gated portfolio metrics** (`GET /api/dashboard/portfolio`): per-project lead time, rework rate, open findings, and monthly budget burn, aggregated server-side.
- **Branch/parent topology validation** moved to PR-open time, catching base-branch mismatches before work is spent rather than at terminal verbs.
- **Cost-tiered complexity routing and named routing presets** in the AI routing surface.

### 4. Post-release panel correctness pass (2026-09-14)

Three contract fixes in the panel's provider settings, each a real defect found by auditing the panel's API types against the backend's Pydantic schemas: the OpenRouter/Nebius model pickers now render and select against the real `OpenRouterModelEntry` contract (previously every row rendered blank and clicking a row selected nothing), the panel's `Task`/`Notification` types dropped phantom fields the backend never sends, and the notifications client stopped sending a `priority_filter` query parameter the backend has never accepted.

## How to run RoboCo on Nebius Token Factory (judge path)

1. `git clone https://github.com/rennf93/roboco.git && cd roboco && make quickstart` (Docker + Compose; pulls pre-built images, bootstraps `.env`, waits for health). Full details in the README's "Running RoboCo" section.
2. Open http://localhost:3000 and sign in as the CEO.
3. Go to **Settings -> AI Routing**, paste your Nebius Token Factory API key into the **Nebius API key** card, and save it (stored Fernet-encrypted server-side, never returned by the API).
4. Click the **Nebius** mode button and confirm. The fleet-wide default model is `nvidia/nemotron-3-super-120b-a12b`; optionally narrow it in the **Nebius default model** search picker.
5. Create a task for any cell. Every spawned agent runs on the opencode CLI against Token Factory on Nemotron; the per-agent usage readers meter tokens and cost, and rate-limited agents park and retry on Nebius's own backoff instead of failing.
6. To see the NVIDIA-model requirement met with your own eyes, open the **Usage** page after the task completes: the per-session rows and the per-model aggregation name `nvidia/nemotron-3-super-120b-a12b` as the model that executed the work.

## License

RoboCo is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0), an OSI-approved open-source license; the full text is in [`LICENSE`](./LICENSE) and GitHub's repo About section detects it as AGPL-3.0. The AGPL's network-use clause (section 13) means anyone running a modified RoboCo as a network service must make the modified source available to its users. Contributions require the Contributor License Agreement ([`CLA.md`](./CLA.md)), which preserves the project's ability to offer a commercial edition while keeping the code open.
