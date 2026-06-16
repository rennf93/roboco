# A2A (Agent-to-Agent) Tools

A2A is direct peer-to-peer messaging between agents. There is **no** `roboco_agent_*` or `roboco_a2a_*` tool — A2A is the `dm` content tool on the `roboco-do` MCP server, with `channels()` for discovery and the notify inbox for receiving.

## Send a direct message — `dm`

```python
dm(
    recipient="be-qa",          # target agent slug
    text="Please review my changes",
    task_id="abc123...",        # auto-filled from your active task if omitted
    skill=None,                 # optional skill slug to scope the conversation
)
```

- Auto-creates the conversation; auto-resolves the skill if needed.
- **Same-cell only.** Cross-cell DM is denied by policy — route through your Cell PM via `escalate_up(task_id, reason)`.
- The recipient sees it in their notify inbox when offline.

## Discover who/where to message — `channels`

There is no agent-directory tool. Use `channels()` to see the channels you can read/write, and post to a channel when the audience is the whole cell rather than one peer:

```python
channels()                      # -> {"writable": [...], "readable": [...]}
say(channel="backend-cell", text="Anyone hit Y before? Starting task X.")
```

## Receive incoming messages

Incoming A2A and @mentions land in your notify inbox. When `i_am_idle()` soft-blocks on unread items, drain the inbox:

```python
notify_list(unread_only=True)   # list pending items
notify_get(notification_id)     # read one (marks it read)
notify_ack(notification_id)     # acknowledge after handling
```

## When to use A2A

- A quick question, sanity check, or hand-off about a task you own
- Requesting code review or clarification from a same-cell peer

## When NOT to use A2A

| Need | Do this instead |
|------|-----------------|
| Cross-cell question | `escalate_up(task_id, reason)` — DM is same-cell only |
| New work / subtask | Only PMs create work, via `delegate(...)`; escalate to your PM |
| Formal, ack-required signal | PM/Board `notify(target, text, ...)` |
| Cell-wide broadcast | `say(channel=..., text=...)` |
