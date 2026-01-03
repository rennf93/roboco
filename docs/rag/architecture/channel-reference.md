# Channel Reference

All available channels with their slugs and access rules.

## Cell Channels

| Slug | Name | Members |
|------|------|---------|
| `backend-cell` | Backend Cell | be-dev-1, be-dev-2, be-qa, be-pm, be-doc |
| `frontend-cell` | Frontend Cell | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc |
| `uxui-cell` | UX/UI Cell | ux-dev-1, ux-dev-2, ux-qa, ux-pm, ux-doc |

## Cross-Cell Channels

| Slug | Name | Members |
|------|------|---------|
| `dev-all` | All Developers | All 6 developers |
| `qa-all` | All QA | be-qa, fe-qa, ux-qa |
| `pm-all` | All PMs | be-pm, fe-pm, ux-pm, main-pm |
| `doc-all` | All Documenters | be-doc, fe-doc, ux-doc |

## Management Channels

| Slug | Name | Members |
|------|------|---------|
| `main-pm-board` | Main PM & Board | main-pm, product-owner, head-marketing, auditor |
| `board-private` | Board Private | product-owner, head-marketing, auditor, ceo |

## Special Channels

| Slug | Name | Read | Write |
|------|------|------|-------|
| `announcements` | Announcements | Everyone | PM/Board only |
| `all-hands` | All Hands | Everyone | Everyone |

## Auditor Silent Access

Auditor has silent read access to:
- `backend-cell`
- `frontend-cell`
- `uxui-cell`
- `dev-all`
- `qa-all`
- `pm-all`
- `doc-all`

Auditor does NOT appear in member lists but CAN read.

## Privileged Access

These roles bypass normal membership checks:
- **CEO**: Full access everywhere
- **Auditor**: Silent read everywhere
- **Main PM**: Read access to all cell channels

## Using Channels

```python
# Send message to your cell
roboco_message_send({
    channel: "backend-cell",
    content: "Starting work on task",
    task_id: task_id
})

# Read channel history
roboco_channel_history("backend-cell", limit=50)

# List available channels
roboco_channel_list()
```
