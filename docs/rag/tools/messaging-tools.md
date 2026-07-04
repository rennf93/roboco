# Notification Tools

There is **no** `roboco_message_*`, `roboco_notify_send`, or `roboco_session_*` tool. Formal notifications are a small set of **content tools** on the `roboco-do` MCP server, role-scoped at spawn time. For agent-to-agent messaging (`dm`, `read_a2a`), see `docs/rag/tools/a2a-tools.md`.

## Formal notification — `notify` (PM / Board only)

`notify` creates an ack-required notification (distinct from the informal `dm`). Only PM roles and the Board may send it; devs / QA / docs reach peers via `dm` and use the inbox tools below to receive.

```python
notify(target="be-dev-1", text="Task ready for you", priority="normal", task_id=task_id)
```

`priority` is `normal | high | urgent`. `task_id` auto-injects from the active task when omitted.

`notify` rejects **human-only recipients** (`prompter`, `secretary`) — they have no agent ack path, so an ack-required alert to them would sit unacked forever. The CEO is allowed (acks via the panel).

## Receiving notifications

Every role with an inbox gets these (so `i_am_idle()` doesn't soft-block on unread items):

```python
notify_list(unread_only=True, limit=20)   # your inbox
notify_get(notification_id)               # read one (marks it read)
notify_ack(notification_id)               # acknowledge after handling
```

When `i_am_idle()` reports unread A2A or @mentions, clear A2A with `read_a2a()` (see `a2a-tools.md`) and clear notifications with list -> get -> ack, then idle again. (The Auditor gets `notify_list`/`notify_get` for inbox visibility but does not ack.)
