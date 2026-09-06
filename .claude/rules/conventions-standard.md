---
paths:
  - "roboco/conventions/**"
  - "roboco/services/conventions.py"
  - "roboco/foundation/policy/conventions/**"
  - ".roboco/**"
---

# Architectural Conventions Standard

Migrated from the root CLAUDE.md so it loads only when the matching files are touched.

**Per-project architectural standard (default-off).** Beyond the `make`-style gates (which check syntax/types/tests, not *where code lives*), each project can carry a repo-canonical `.roboco/conventions.yml` - an architecture map (which definition *kinds* belong in which modules), a toggleable rule set, custom regex rules, and waivers - so an agent cannot land a Pydantic model defined inside a router or a `# noqa` / `# type: ignore`. Placement of a *helper* (any top-level function) only **warns** - too blunt to hard-block; `thin_routes` doesn't count an explicit `db.commit()`; and a small allowlist of unavoidable framework suppressions (ruff `TC001`-`TC003`, pydantic `prop-decorator`) is exempt. Gated by `ROBOCO_CONVENTIONS_ENABLED`; fully inert when off. RoboCo itself ships a canonical `.roboco/conventions.yml`.

**Effective map.** Consumers read the *effective* map - auto-derived defaults (from a repo scan + `BUILTIN_RULES`, excluding `tests/`/`docs/` trees) overlaid by the committed file - so behaviour is identical whether the file is present, absent, or partial. `ConventionsService` (`roboco/services/conventions.py`) builds it, caches it per `(project, HEAD sha)` in `project_conventions_cache` (migration `043`), renders the per-task baseline constraints + the ambient prompt block, and scaffolds/restores the file via a PR (`GitService.open_conventions_pr`). The committed file + scan are read from a dedicated project-level **read clone** the service ensures on demand (`WorkspaceService.ensure_read_clone`, pinned to the default branch's HEAD) - the backfill that makes the standard resolve even for a project created before it existed, with no manual `workspace_path`. The schema lives in `roboco/foundation/policy/conventions/` (pure).

**Validator.** A single Python CLI, `python -m roboco.conventions check --root <repo> --files <a> <b> ...` (`roboco/conventions/`), uses tree-sitter (Python + TypeScript grammars, shipped in the agent image) to classify each changed definition and flag forbidden placements + hygiene + custom-rule matches as JSONL findings, after waiver filtering. Precision over recall (it abstains when uncertain so a `block` gate can't false-positive-strand a task) and fail-loud (a validator that cannot run exits 3 so the gate blocks, never silently passes).

**Threading + enforcement.** The standard reaches the work two ways: an ambient "Architectural Standard" block injected at spawn (`compose_prompt`) and an auto-attached `## Constraints` section on every project task (`TaskService.create`). Enforcement is deterministic: a `block`-level finding refuses `i_am_done` (dev pre-submit) and `pr_pass` (the in-path PR gate) with the offending `file:line` + fix hint; findings also surface in QA's `claim_review` evidence (`convention_findings`). A false positive is relieved by a `waiver` the dev commits in their branch - accountable, reviewed in the PR. The panel's per-project Conventions tab (a page-level tab on `/projects/[id]/settings`, Wave C - was a tab inside the edit-project dialog) shows the map + health and offers Save / Restore.

