# Optional subsystems

Beyond the core delivery loop, RoboCo ships a set of optional engines — most **off by default** — that you turn on when you want them. They're the "company-in-a-box" capabilities and the stricter quality gates. This page is the reference for all of them; each has its own page below.

## The Feature Flags card

You toggle these from **Settings → Feature Flags** in the panel rather than hand-editing environment variables. A toggle persists in the settings store and **takes effect on the next backend restart**. The matching `ROBOCO_*` environment variable is the same switch at the source — an unset flag falls back to its environment/config default, and some flags carry extra configuration (an API key, a project slug) that only lives in the environment.

!!! info "The restart contract"
    Flipping a flag in the panel saves it immediately, but the subsystem it controls is wired up at startup — so the change lands on the **next backend restart**, not instantly.

## What you can turn on

| Subsystem | Flag | Default | What it does |
|-----------|------|---------|--------------|
| [Architectural Conventions](conventions.md) | `ROBOCO_CONVENTIONS_ENABLED` | off | Per-project rules for *where code lives*; hard-gates agents from misplaced code and lint suppressions. |
| [Toolchain matching](toolchain-matching.md) | `ROBOCO_TOOLCHAIN_MATCH_ENABLED` | off (on in the personal compose) | Builds each project under its own declared Python and blocks gates when the suite can't run. |
| [Web research](web-research.md) | `ROBOCO_RESEARCH_ENABLED` | off | Gives Board/PM agents gated web search & fetch through a provider you supply. |
| [Strategy engine](strategy-engine.md) | `ROBOCO_STRATEGY_ENGINE_ENABLED` | off | Notify-only nudges when the company drifts, goes idle, or stalls. |
| [Pitch provisioning](pitch-provisioning.md) | `ROBOCO_PROVISIONING_TOKEN` (+ org) | inert until set | On pitch approval, auto-creates repos and seeds a build task. |
| [External / internal PR review](pr-review.md) | `ROBOCO_EXTERNAL_PR_ENABLED` / `ROBOCO_INTERNAL_PR_ENABLED` | off | Reviews inbound external/fork PRs and untied org-repo PRs. |
| [Self-healing CI](self-heal.md) | `ROBOCO_SELF_HEAL_ENABLED` (+ originate) | off | Watches RoboCo's own CI and, optionally, queues a CEO-gated fix task. |
| [Multi-repo CI-watch](autonomous-maintenance.md) | `ROBOCO_CI_WATCH_ENABLED` (+ per-project) | off | Watches each opted-in project's CI and opens one fix task when it goes red; never auto-merges. |
| [Dependency-update bot](autonomous-maintenance.md) | `ROBOCO_DEP_UPDATE_ENABLED` (+ per-project) | off | Read-only checks whether an upgrade changes a project's lockfiles and opens an update task; never auto-merges. |

!!! note "Always-on resilience"
    Provider overload parking (`ROBOCO_OVERLOAD_BREAK_ENABLED`) and the dangling-image prune (`ROBOCO_IMAGE_PRUNE_ENABLED`) are **on by default** — they're not things you enable, they're safety nets you can disable. See [Resilience](../models/resilience.md).
