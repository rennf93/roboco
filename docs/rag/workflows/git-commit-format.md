# Git Commit Format

Commits use conventional format with traceability:

```
[{root-id:8}:{task-id:8}] {type}({scope}): {description}

{body}

---
Task: {task-id}
Root: {root-task-id}
Agent: {agent-slug}

Links:
- Task: {api}/tasks/{task-id}
- Root: {api}/tasks/{root-task-id}
- Journal: {api}/journals/{agent-slug}
```

**Required:** the conventional `type` (feat, fix, chore, docs, refactor, test, style, perf, ci, build) at the start of the subject. The `commit_validator` rejects messages that don't start with one of these followed by `(scope)?:`.

**Optional:** `scope` (api, auth, db, ui), `body`, the `files` argument to scope the commit.

## How to commit

Use the **`commit`** verb on the roboco-do MCP — devs and documenters only. There is no `roboco_git_commit` tool.

```python
commit(
    message="feat(api): add Redis rate limiter",
    files=["roboco/api/routes/rate.py", "tests/integration/test_rate.py"],
    # files is optional; defaults to all staged + modified tracked files
)
```

The choreographer:

1. Strips any leading `[task-id]` you might have included
2. Runs `commit_validator` on the subject
3. Re-prefixes with the canonical `[task-id-first-8]`
4. Stages the listed files (or everything tracked + modified)
5. Commits in the agent's workspace
6. Pushes to the agent's branch on origin
7. Records the commit on the task (`commits[]` field on `TaskTable`)

You don't need a separate `push` step. There is no `roboco_git_push` tool.
