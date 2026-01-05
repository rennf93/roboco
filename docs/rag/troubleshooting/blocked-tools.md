# Blocked Tools

## Native Git Commands Blocked

**Symptom:** `Bash(git commit)` or similar git command denied

**Cause:** Native git commands are blocked for all agents

**Solution:** Use MCP tools instead:
| Blocked | Use Instead |
|---------|-------------|
| `git commit` | `roboco_git_commit()` |
| `git push` | `roboco_git_push()` |
| `git status` | `roboco_git_status()` |
| `git diff` | `roboco_git_diff()` |
| `git log` | `roboco_git_log()` |

## Write/Edit Outside Workspace

**Symptom:** `Write()` or `Edit()` denied for a file path

**Cause:** Write operations restricted to your workspace

**Solution:**
- Developers: Only write in `/data/workspaces/{project}/{team}/{agent-id}/`
- Documenters: Only write in `/app/docs/`
- QA: No write access (review only)

## QA Cannot Commit

**Symptom:** `roboco_git_commit()` denied for QA agent

**Cause:** QA role is read-only, cannot modify code

**Solution:** QA reviews and provides feedback. Developers make fixes.

## NO_PLAN Error

**Symptom:** `roboco_task_start()` returns NO_PLAN error

**Cause:** Task has no plan submitted

**Solution:** Call `roboco_task_plan()` before `roboco_task_start()`

See: `roboco_kb_search("task planning workflow")`

## WAITING_FOR_BRANCH

**Symptom:** Can't start git task, state is WAITING_FOR_BRANCH

**Cause:** PM hasn't created the branch yet

**Solution:**
1. Message PM to create branch
2. Or escalate: `roboco_task_escalate(task_id, "Need branch")`
3. Wait for branch_name to be set on task
