# Git Error Troubleshooting

## Missing Git Token

**Error**: "Project requires a git token for HTTPS repositories"

**Cause**: No GitHub PAT configured for this project

**Solutions**:
1. Open project settings in UI
2. Add GitHub token (Personal Access Token)
3. Token needs `repo` scope for clone/push/PR

**Notes**:
- Each project requires its own token (no global fallback)
- Tokens are encrypted at rest
- Token never exposed in API responses

## Workspace Not Found

**Error**: "Workspace does not exist"

**Cause**: Workspace not cloned yet

**Solutions**:
- If auto_clone enabled: workspace creates on first access
- Manual: Wait for workspace service to clone
- Check config: `ROBOCO_WORKSPACE_AUTO_CLONE=true`

## Cannot Push

**Error**: "Push failed"

**Causes**:
1. No commits to push
2. Remote branch doesn't exist
3. Conflicts with remote

**Solutions**:
- Create commits first: `roboco_git_commit(...)`
- Check branch exists: `roboco_git_branches()`
- Pull and resolve conflicts

## Branch Already Exists

**Error**: "Branch already exists"

**Cause**: Trying to create existing branch

**Solution**: Checkout existing branch:
```python
roboco_git_checkout(project_slug, branch_name)
```

## Merge Conflicts

**Error**: "Merge conflict"

**Cause**: Conflicting changes between branches

**Solutions**:
1. Pull latest from target branch
2. Resolve conflicts manually
3. Commit resolution
4. Push again

## PR Creation Failed

**Error**: "PR creation failed"

**Causes**:
1. No commits on branch
2. Branch not pushed
3. GitHub CLI not configured

**Solutions**:
- Push branch first: `roboco_git_push()`
- Verify commits exist: `roboco_git_log()`

## Checkout Failed

**Error**: "Cannot checkout - uncommitted changes"

**Cause**: Working directory has uncommitted changes

**Solutions**:
- Commit changes: `roboco_git_commit(...)`
- Or stash changes (if supported)
