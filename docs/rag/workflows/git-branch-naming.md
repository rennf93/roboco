# Git Branch Naming

Branches follow: `{type}/{team}/{root-uuid}[/{subtask-uuid}[/{sub-sub-uuid}]]`

**Types:** `feature`, `bug`, `chore`, `docs`, `hotfix`

**Max depth:** 3 levels (root → subtask → sub-subtask)

**Examples:**
- Root: `feature/backend/550e8400-e29b-41d4-a716-446655440000`
- Subtask: `feature/backend/550e8400.../6ba7b810...`
- Sub-sub: `feature/backend/550e8400.../6ba7b810.../f47ac10b...`

**Branches are auto-created when tasks are claimed.** No manual creation needed.
