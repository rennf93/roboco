# Channel Structure

## Channel Types

| Type | Purpose | Example |
|------|---------|---------|
| `cell` | Internal team | #backend-cell |
| `cross_cell` | Role coordination | #dev-all, #qa-all |
| `management` | PM/Board | #main-pm-board |
| `special` | Announcements | #all-hands |

## Cell Channels

| Channel | Members |
|---------|---------|
| #backend-cell | be-pm, be-dev-*, be-qa, be-doc |
| #frontend-cell | fe-pm, fe-dev-*, fe-qa, fe-doc |
| #uxui-cell | ux-pm, ux-dev-*, ux-qa, ux-doc |

## Cross-Cell Channels

| Channel | Members |
|---------|---------|
| #dev-all | All developers |
| #qa-all | All QAs |
| #pm-all | All PMs |
| #doc-all | All documenters |

## Management Channels

| Channel | Members |
|---------|---------|
| #main-pm-board | Main PM, Board |
| #board-private | Board only |

## Special Channels

| Channel | Access |
|---------|--------|
| #announcements | Read: all, Write: PM/Board |
| #all-hands | Read/Write: all |

## Auditor Access

Auditor has **silent read access** to ALL channels:
- Does not appear in member lists
- Cannot send messages
- Observes all activity

## Channel Access Rules

| Role | Own Cell | Cross-Cell | Management |
|------|----------|------------|------------|
| Developer | Read/Write | Read/Write | - |
| QA | Read/Write | Read/Write | - |
| Documenter | Read/Write | Read/Write | - |
| Cell PM | Read/Write | Read/Write | - |
| Main PM | Read/Write | Read/Write | Read/Write |
| Board | - | - | Read/Write |
| Auditor | Silent Read | Silent Read | Silent Read |

## Messaging

```python
roboco_message_send({
    channel: "backend-cell",
    content: "Starting work on rate limiting",
    task_id: task_id
})
```
