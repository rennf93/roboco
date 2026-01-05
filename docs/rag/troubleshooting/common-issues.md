# Common Issues

## Permission Denied

**Error**: "Not authorized for this action"

**Cause**: Your role doesn't have permission

**Check Permissions**:
| Action | Allowed Roles |
|--------|---------------|
| Create task | PM, Board |
| Cancel task | PM |
| Pass/fail QA | QA only |
| Complete docs | Documenter only |
| Complete task | PM only |
| Send notification | PM, Board |

**Solution**: Request appropriate role to perform action

## Task Stuck in Status

**Problem**: Task won't transition

**Causes**:
1. Missing required fields
2. Waiting on parallel action
3. Invalid transition attempted

**Check**:
- For git tasks: branch exists?
- For `awaiting_pm_review`: both `docs_complete` AND `pr_created`?
- Is transition valid from current status?

## Notification Not Received

**Problem**: Expected notification didn't arrive

**Causes**:
1. Sender doesn't have notification permission
2. Notification filtering
3. Already acknowledged

**Solutions**:
- Check `roboco_notify_list()` for all notifications
- Verify sender has PM/Board role
- Check if already in `acked_by`

## Escalation Not Routing

**Problem**: Escalation went to wrong person

**Cause**: Escalation auto-routes to your escalation target

**Chain**:
```
Developer → Cell PM → Main PM → Product Owner → CEO
```

Cannot skip levels or choose target.

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
- Read channel history for discussions

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

**Problem**: Sent A2A message but no response

**Check**:
1. Did you include `task_id`? (required)
2. Check delivery status in response: `"direct"` or `"notification"`
3. If `"notification"` - target was offline, will be spawned

**Solutions**:
- Direct delivery: Target should check `roboco_a2a_check()`
- Notification delivery: Wait for target to be spawned

## A2A SDK Server Unavailable

**Error**: "SDK Server is not available"

**Cause**: SDK Server not running in container

**Solution**: SDK Server starts automatically with agent container. If error persists, container may need restart.
