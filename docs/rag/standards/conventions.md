# Architectural Conventions Standard

A per-project, repo-canonical standard for *where code lives* and basic house-style hygiene — the layer above the `make`-style gates (which check syntax, types, and tests, not placement). It exists so an agent cannot land a model defined inside a router, a helper in a route file, or a lint suppression, even when the code compiles and the tests pass.

The standard is gated by `ROBOCO_CONVENTIONS_ENABLED` (default off) and is fully inert when off.

## How a project declares it

Each project carries a repo-canonical `.roboco/conventions.yml`. It is auto-scaffolded into a project's clone the first time the project is worked on, editable from the per-project **Conventions** tab in the panel, and committed like any other repo file.

Consumers always read the *effective* map: auto-derived defaults (from a repo scan plus the built-in rules) overlaid by the committed file. Behaviour is identical whether the file is present, absent, or partial — a missing file just means "defaults only".

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

# Toggle or re-level the built-in rules.
rules:
  no_models_in_routers: { level: block }   # block | warn | off
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

## Where it is enforced

Enforcement is deterministic and reaches the work two ways: an ambient "Architectural Standard" block injected into an agent's context at spawn, and an auto-attached `## Constraints` section on every project task.

- **Developer pre-submit** — a `block`-level finding refuses `i_am_done` with the offending `file:line` and a fix hint.
- **In-path PR gate** — the same finding refuses the reviewer's `pr_pass`.
- **QA review** — findings surface as `convention_findings` in the evidence QA sees when it claims a review.

A `warn`-level finding is reported but never blocks.

## Clearing a false positive

A false positive is relieved by a **waiver** the developer commits in their branch — so the escape is accountable and reviewed in the PR, not a silent in-code suppression (`# noqa` / `# type: ignore` are themselves hygiene violations the standard flags). Add the waiver to `.roboco/conventions.yml`, commit it, and the finding is filtered on the next check.

## Panel

The per-project **Conventions** tab (in the edit-project dialog) shows the effective architecture map and its health, and offers **Save** (commit an edited map back to the repo via a PR) and **Restore** (re-scaffold the canonical file).
