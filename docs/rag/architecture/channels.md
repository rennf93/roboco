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
| #main-pm-board | Main PM, Product Owner, Head Marketing, Auditor |
| #board-private | Product Owner, Head Marketing, Auditor, CEO, Main PM |

In both management channels the Auditor has read **and** write (it is NOT
silent here — that downgrade applies only to the cell and cross-cell
channels). In #board-private the Main PM can read but cannot write.

## Special Channels

| Channel | Access |
|---------|--------|
| #announcements | Read: all, Write: PM/Board |
| #all-hands | Read/Write: all |

## Auditor Access

Auditor has **silent read access** to the cell and cross-cell channels:
- Does not appear in member lists
- Cannot send messages there
- Observes all activity

The Auditor is silent only on cell + cross-cell channels (it is in those
channels' `silent_roles`). On the management channels (#main-pm-board,
#board-private) it has full read + write. The Auditor's content-tool
manifest is `note(scope=reflect)` + `evidence` + read-only
`notify_list`/`notify_get`/`channels` — it has no `say`/`dm`/`notify`, so in
practice it observes rather than posts.

## Channel Access Rules

| Role | Own Cell | Cross-Cell | Management |
|------|----------|------------|------------|
| Developer | Read/Write | Read/Write | - |
| QA | Read/Write | Read/Write | - |
| Documenter | Read/Write | Read/Write | - |
| Cell PM | Read/Write | Read/Write | #pm-all (Read/Write) |
| Main PM | Read/Write | Read/Write | Read/Write |
| Board | - | - | Read/Write |
| Auditor | Silent Read | Silent Read | Read/Write |

## Messaging

Agents post to channels with the `say` content tool (there is no
`roboco_message_send` tool):

```python
say(
    channel="backend-cell",
    text="Starting work on rate limiting",
    task_id=task_id,
)
```

For direct agent-to-agent messages, use `dm(recipient, text)` (same-cell
only; cross-cell is denied — escalate via your Cell PM instead). PMs and the
Board can additionally send ack-required notifications with
`notify(target, text, priority)`.
