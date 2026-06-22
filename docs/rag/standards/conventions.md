# Architectural Conventions Standard

A per-project, repo-canonical standard for *where code lives*, how a definition is *built*, and basic house-style hygiene — the layer above the `make`-style gates (which check syntax, types, and tests, not placement or structure). It exists so an agent cannot land a model defined inside a router, a route handler that runs its own database access, or a lint suppression, even when the code compiles and the tests pass (a misplaced *helper* — any top-level function — only warns, since that signal is too blunt to hard-block). It enforces the separation of concerns a senior would demand in review, not just linting.

The standard is gated by `ROBOCO_CONVENTIONS_ENABLED` (default off) and is fully inert when off.

## How a project declares it

Each project carries a repo-canonical `.roboco/conventions.yml`. It is auto-scaffolded into a project's clone the first time the project is worked on, editable from the per-project **Conventions** tab in the panel, and committed like any other repo file.

Consumers always read the *effective* map: auto-derived defaults (from a repo scan plus the built-in rules) overlaid by the committed file. Behaviour is identical whether the file is present, absent, or partial — a missing file just means "defaults only". The scan excludes test and documentation trees (`tests/`, `docs/`) — those legitimately define fixtures and aren't enforced code.

The committed file and the scan are read from a project-level **read clone** the service ensures on demand (pinned to the default branch's HEAD), not from any agent's working clone — so the standard resolves even for a project created long before it existed, with no manual workspace configuration.

```yaml
# .roboco/conventions.yml
version: 1
languages: [python, typescript]

# Which definition KINDS each module may and may not contain.
modules:
  - path: app/routers
    purpose: HTTP routing only
    forbidden: [model, helper]      # no Pydantic models or helpers in routers
  - path: app/models
    purpose: data models
  - path: app/services
    purpose: business logic + side effects

# Toggle or re-level the built-in rules (each fires at `warn` or `block`).
rules:
  no_models_in_routers: { level: block }
  no_inline_comments: { level: warn }

# Project-specific regex rules.
custom:
  - name: no_print
    pattern: "\\bprint\\("
    level: warn
    message: "Use the logger, not print()."

# Reviewed escapes for a genuine false positive.
waivers:
  - rule: no_models_in_routers
    path: app/routers/legacy.py
    reason: "Legacy shim, scheduled for removal."
```

## The validator

A single Python CLI classifies every changed definition with tree-sitter (Python and TypeScript grammars, shipped in the agent image) and reports forbidden placements, hygiene violations, and custom-rule matches as JSONL findings, after waiver filtering:

```bash
python -m roboco.conventions check --root <repo> --files <a> <b> ...
```

It favours precision over recall — it abstains when it cannot classify a definition, so a `block` gate can never strand a task on a guess — and it fails loud: a validator that cannot run exits non-zero so the gate blocks rather than silently passing.

## Modularity

Beyond placement and hygiene, the standard enforces modularization with a **modularity** check family. Where placement asks *which module a definition belongs in*, modularity inspects a definition's **body** and a file's **composition** — the structural questions a senior asks in code review:

- **`modular_cohesion`** (any stack) — a file must own one architectural concern. A file that mixes them (a Pydantic model defined in a router, a schema defined in a component) is a monolith to split.
- **`thin_routes`** (Python / API) — a route handler must delegate to a service. It may not run its own database access (no `session.execute` / `query` / `scalars` / `add`, no `select()` / `insert()` / `update()` / `delete()`) in the route body. Transaction-lifecycle calls — `commit` / `flush` / `refresh` — do not count: an explicit `await db.commit()` after delegating to a service is a valid pattern.
- **`thin_components`** (TypeScript / React) — a component must stay presentational. Data fetching (`fetch` / `axios`) belongs in a hook, not in the component body.
- **`god_class`** (any stack) — a class past a method-count threshold is doing too much; decompose it to keep a single responsibility.

Rules are scan-derived and language-aware: hygiene seeds universally, placement only for modules that actually exist in the repo, and modularity per stack — a Python project gets `thin_routes`, a TypeScript project gets `thin_components`, and `modular_cohesion` plus `god_class` apply to both. A frontend project therefore carries `no_models_in_components` and `thin_components`, never a backend `no_models_in_routers`.

Modularity findings flow through the same enforcement as the rest of the standard: a `block`-level finding refuses the developer's `i_am_done` and the reviewer's `pr_pass` with the offending `file:line` and a fix hint, and surfaces in QA's `claim_review` evidence as `convention_findings`. A false positive is cleared the same way — a waiver committed in the branch.

## Where it is enforced

Enforcement is deterministic and reaches the work two ways: an ambient "Architectural Standard" block injected into an agent's context at spawn, and an auto-attached `## Constraints` section on every project task.

- **Developer pre-submit** — a `block`-level finding refuses `i_am_done` with the offending `file:line` and a fix hint.
- **In-path PR gate** — the same finding refuses the reviewer's `pr_pass`.
- **QA review** — findings surface as `convention_findings` in the evidence QA sees when it claims a review.

A `warn`-level finding is reported but never blocks.

## Clearing a false positive

A false positive is relieved by a **waiver** the developer commits in their branch — so the escape is accountable and reviewed in the PR, not a silent in-code suppression (`# noqa` / `# type: ignore` are themselves hygiene violations the standard flags). Add the waiver to `.roboco/conventions.yml`, commit it, and the finding is filtered on the next check.

The one exception to "suppressions are violations" is a small allowlist of *structurally unavoidable* framework codes that the validator does not flag: ruff's flake8-type-checking codes (`TC001`–`TC003`, for an import a framework needs at runtime) and pydantic's `prop-decorator`. A bare `# noqa` / `# type: ignore` or any other code is still a finding.

## Panel

The per-project **Conventions** tab (in the edit-project dialog) shows the effective architecture map and its health, and offers **Save** (commit an edited map back to the repo via a PR) and **Restore** (re-scaffold the canonical file).
