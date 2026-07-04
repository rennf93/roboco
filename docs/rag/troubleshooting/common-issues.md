# Common Issues

## Permission Denied

**Error**: "Not authorized for this action"

**Cause**: Your role doesn't have permission

**Check Permissions**:
| Action | Allowed Roles |
|--------|---------------|
| Create / delegate task | PM only (Cell PM, Main PM) |
| Cancel task | PM, CEO |
| Pass/fail QA | QA only |
| Complete docs | Documenter only |
| Complete task | PM only |
| Send notification (`notify`) | PM, Board |

**Solution**: Request appropriate role to perform action

## Task Stuck in Status

**Problem**: Task won't transition

**Causes**:
1. Missing required fields
2. Waiting on parallel action
3. Invalid transition attempted

**Check**:
- Branch exists?
- For `awaiting_pm_review`: both `docs_complete` AND `pr_created`?
- Is transition valid from current status?

## Notification Not Received

**Problem**: Expected notification didn't arrive

**Causes**:
1. Sender doesn't have notification permission
2. Notification filtering
3. Already acknowledged

**Solutions**:
- Check `notify_list()` for all notifications
- Verify sender has PM/Board role
- Check if already acknowledged via `notify_get(notification_id)`

## Escalation Not Routing

**Problem**: Escalation went to wrong person

**Cause**: `escalate_up` auto-routes to your escalation target

**Chain**:
```
Cell members → Cell PM → Main PM → Product Owner → CEO
```

Cannot skip levels or choose target. (Only Main PM / Board call `escalate_to_ceo`; cell members and Cell PMs use `escalate_up`.)

## Tests Failing Before Submit

**Problem**: Tests fail, can't submit to QA

**Checklist**:
```bash
# Backend
uv run pytest           # Tests
uv run ruff check .     # Linting
uv run mypy roboco/     # Type check

# Frontend
pnpm test
pnpm lint
pnpm typecheck
```

Fix all issues before submitting.

## Lost Context After Pause

**Problem**: Resuming task, forgot context

**Solutions**:
- Read `quick_context` field on task
- Read your journal for this task
- Get proactive context: `roboco_get_proactive_context(task_id)`

## Documentation Path Confusion

**Problem**: Unsure where to write documentation

**Solution**: Use `roboco_docs_write()` - system handles paths automatically

```python
roboco_docs_write({
    "task_id": "your-task-uuid",
    "filename": "feature.md",
    "doc_type": "api",  # api, qa, guide, readme, changelog, architecture, design
    "title": "Feature Documentation",
    "content": "..."
})
```

- Team folder: Determined from your agent ID
- Subfolder: Determined by doc_type
- No path decisions needed

## Documentation Already Exists

**Problem**: Want to update existing doc but created duplicate

**Cause**: Content was too different from existing doc (RAG similarity < 0.75)

**Solution**:
- Ensure content covers the same topic
- Or delete duplicate: `roboco_docs_delete(path)`
- Check existing: `roboco_docs_list(task_id)` or `roboco_kb_search("topic")`

**Note**: `roboco_docs_write()` auto-deduplicates via RAG by **content similarity**. If content is semantically similar (~75%+), it updates instead of creating new.

## A2A Message Not Delivered

**Problem**: Sent a `dm` but no response

**Check**:
1. Is the recipient in your **own cell**? Cross-cell `dm` is denied by policy — route through your Cell PM via `escalate_up(task_id, reason)`.
2. Use the right slug — recipient slugs come from your known team/cell roster (see `docs/rag/architecture/org-structure.md`'s Cells table), not a runtime discovery call.
3. Did you include `task_id`? It anchors the message to the work.

**Solutions**:
- Same-cell peer: `dm(recipient="be-qa", text="...", task_id="...")`
- Anything cross-cell or needing PM action: `escalate_up(task_id, reason)`

## Cross-Cell Message Denied

**Error**: A `dm` to an agent outside your cell is rejected by policy

**Cause**: Direct A2A is same-cell only — there is no cross-cell `dm`

**Solution**: Escalate up the chain. Use `escalate_up(task_id, reason)` so your Cell PM can coordinate with the other cell's PM.
