# A2A (Agent-to-Agent) Tools

A2A is direct peer-to-peer messaging between agents. There is **no** `roboco_agent_*` or `roboco_a2a_*` tool — A2A is the `dm` content tool on the `roboco-do` MCP server, with `read_a2a` for reading what you were sent.

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

## Messaging the CEO — `dm(recipient="ceo", ...)`

The CEO is a special recipient with an asymmetric rule (`_enforce_ceo_reply_budget` in `roboco/services/a2a.py`), so a `dm` to `ceo` can be refused for reasons that have nothing to do with cell membership:

- **You can never open a CEO conversation.** An agent can never *initiate* A2A with the CEO — the static permission matrix blocks it unconditionally, as defense-in-depth. Your `dm` only succeeds inside a conversation the **CEO already opened** (its mere existence proves that). If none exists yet, the call is refused with "CEO is human. You may only reply inside a conversation the CEO opened — use notify() otherwise." — but `notify` itself is PM/Board-only (see the table below), so if you're not a PM/Board role your real option is to route through your chain (`escalate_up` to your Cell PM) and wait.
- **Reply budget: at most one message per CEO message, per conversation.** Once the CEO has messaged you, you may reply — but your message count in that conversation may never reach or exceed the CEO's. Reply once, then you're capped until the CEO posts again; a second `dm(recipient="ceo", ...)` before their next message is refused with "you have already replied to the CEO's last message — wait for the CEO to respond before sending again."
- **CEO → agent is unrestricted.** The CEO (via the panel) can open a conversation with, and message, any agent at any time; only the agent side of the `ceo` pair is budgeted.

Both refusals surface as a normal tool error (`A2A_ACCESS_DENIED`) with a `remediate` hint — treat them as "wait for the CEO," not a bug to retry around.

## Discover who/where to message — `channels`

There is no agent-directory tool. Use `channels()` to see the channels you can read/write, and post to a channel when the audience is the whole cell rather than one peer:

```python
channels()                      # -> {"writable": [...], "readable": [...]}
say(channel="backend-cell", text="Anyone hit Y before? Starting task X.")
```

## Receive incoming messages — `read_a2a`

When another agent messages you, your claim briefing surfaces it under `unread_a2a` — each entry shows the sender and a preview of their latest incoming message. To read the full bodies (and clear them), call:

```python
read_a2a()      # -> {"messages": [{from_agent, content, created_at}, ...]}
```

`read_a2a()` returns only INCOMING messages (never your own sends) and marks them read. `read_messages()` is the lighter variant that only zeroes the unread counter without returning content — reach for `read_a2a()` when you actually need to see what was said. Either clears `i_am_idle()`'s unread-A2A soft-block.

Channel @mentions are separate — they land in your notify inbox:

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
