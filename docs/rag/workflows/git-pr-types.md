# Git PR Types

| `is_root_pr` | Target | Reviewer | Content |
|--------------|--------|----------|---------|
| `True` | main | CEO | Full task tree, all commits, all agent links |
| `False` | parent branch | PM | Simple summary, task commits only |

**Auto-checkout:** `roboco_task_start()` checks out branch automatically. Blocks if uncommitted changes exist.

**PR creation:** Use `roboco_git_create_pr()`. Title/body auto-generated from templates.
