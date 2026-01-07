# Git Commit Format

Commits use conventional format with traceability:

```
[{root-id:8}:{task-id:8}] {type}({scope}): {description}

{body}

---
Task: {task-id}
Root: {root-task-id}
Agent: {agent-slug}
Session: {session-id}

Links:
- Task: {api}/tasks/{task-id}
- Root: {api}/tasks/{root-task-id}
- Journal: {api}/journals/{agent-slug}
```

**Required:** `commit_type` (feat, fix, chore, docs, refactor, test, style, perf, ci, build)

**Optional:** `scope` (api, auth, db, ui), `body`, `files`

Use `roboco_git_commit()` - message built automatically with task context.
