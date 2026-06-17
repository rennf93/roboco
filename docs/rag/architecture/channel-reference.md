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
| `main-pm-board` | Main PM & Board | main-pm, product-owner, head-marketing, auditor (all read/write) |
| `board-private` | Board Private | product-owner, head-marketing, auditor, ceo (read/write) + main-pm (read-only) |

## Special Channels

| Slug | Name | Read | Write |
|------|------|------|-------|
| `announcements` | Announcements | Everyone | PM/Board only |
| `all-hands` | All Hands | Everyone | Everyone |

## Auditor Silent Access

Auditor has silent read access (in these channels' `silent_roles`) to:
- `backend-cell`
- `frontend-cell`
- `uxui-cell`
- `dev-all`
- `qa-all`
- `pm-all`
- `doc-all`

Auditor does NOT appear in member lists but CAN read. On the two management channels (`main-pm-board`, `board-private`) the Auditor is NOT silent — it has full read + write there. (Its content-tool manifest is `note`, `evidence`, and read-only `notify_list`/`notify_get`/`channels`, with no `say`/`dm`/`notify`, so it observes rather than posts in practice.)

## Privileged Access

These roles bypass normal membership checks:
- **CEO**: Full access everywhere
- **Auditor**: Silent read on cell + cross-cell channels; read/write on the management channels
- **Main PM**: Read access to all cell channels

## Using Channels

```python
# List the channel slugs you can read / write (call this first if unsure of
# a slug — inventing slugs returns "Channel not found")
channels()   # -> {writable: [...], readable: [...]}

# Send a message to your cell
say(
    channel="backend-cell",
    text="Starting work on task",
    task_id=task_id,
)

# Direct agent-to-agent message (same-cell only)
dm(recipient="be-qa", text="Quick sanity check before QA", task_id=task_id)
```
