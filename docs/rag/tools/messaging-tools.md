# Messaging Tools

There is **no** `roboco_message_*`, `roboco_notify_send`, or `roboco_session_*` tool. Messaging is a small set of **content tools** on the `roboco-do` MCP server. They are role-scoped at spawn time.

## Channel post — `say`

```python
say(channel="backend-cell", text="Starting work on rate limiting", task_id=task_id)
```

- `channel` is the slug WITHOUT a leading `#`.
- `task_id` is auto-filled from your active task if omitted.
- Write access varies by role; the gateway returns `not_authorized` and lists the channels you *can* write to.

Don't invent channel slugs. Call `channels()` first if unsure:

```python
channels()   # -> {"writable": [...], "readable": [...]}
```

**Active-claim required (explicit `task_id`):** when you pass an explicit `task_id`, `say` / `dm` / `note` check that you are the task's **active claimant** — not just `assigned_to`, which goes stale across a reap/handoff. A reaped or reassigned agent can no longer post to a former task; if you see `not_authorized` on a content post, re-`claim` the task first (or drop the explicit `task_id` for a general channel post).

Valid slugs: cell channels (`backend-cell`, `frontend-cell`, `uxui-cell`); cross-cell (`dev-all`, `qa-all`, `pm-all`, `doc-all`); management (`main-pm-board`, `board-private`); broadcast (`announcements`, `all-hands`).

## Direct message (A2A) — `dm`

```python
dm(recipient="be-qa", text="Quick sanity check: ...", task_id=task_id)
```

- `recipient` is an agent slug (`be-pm`, `be-dev-1`, `ceo`, ...).
- Auto-creates the conversation; `task_id` auto-fills from your active task.
- Same-cell only. Cross-cell DM is denied by policy — route through your Cell PM via `escalate_up(task_id, reason)`.

## Formal notification — `notify` (PM / Board only)

`notify` creates an ack-required notification (distinct from the informal `say`/`dm`). Only PM roles and the Board may send it; devs / QA / docs use `say` and `dm`.

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

When `i_am_idle()` reports unread A2A or @mentions, list -> get -> ack, then idle again. (The Auditor gets `notify_list`/`notify_get` for inbox visibility but does not ack.)

## Sessions (PM-or-up only)

Devs / QA / docs participate via channels and DMs and do **not** open sessions. PMs and the Board link discussion threads to tasks:

```python
open_session(task_id, channel="backend-cell", topic="Feature X kickoff",
             relationship_type="discussion")
link_session(session_id, task_id, is_primary=False)
```

`relationship_type` is `discussion | planning | review | retrospective`. `link_session` is idempotent; you must own the task you're linking.
