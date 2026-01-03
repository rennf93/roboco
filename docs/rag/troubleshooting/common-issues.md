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
