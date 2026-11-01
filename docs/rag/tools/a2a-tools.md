# A2A (Agent-to-Agent) Tools

A2A is direct peer-to-peer messaging between agents. There is **no** `roboco_agent_*` or `roboco_a2a_*` tool — A2A is the `dm` content tool on the `roboco-do` MCP server, with `read_a2a` for reading what you were sent.

## Send a direct message — `dm`

```python
dm(
    recipient="be-qa",  # target agent slug
    text="Please review my changes",
    task_id="abc123...",  # omitted: the one task you hold the claim on (see below)
    skill=None,  # optional skill slug to scope the conversation
)
```

- Auto-creates the conversation; auto-resolves the skill if needed.
- **Same-cell only.** Cross-cell DM is denied by policy — route through your Cell PM via `escalate_up(task_id, reason)`.
- The recipient sees it in their notify inbox when offline.
- **A cell PM writing about its subtree:** a cell PM may `dm` about any task under a task it is assigned to that task's assignee in its own cell, e.g. `dm(recipient='fe-dev-1', task_id='<fe-dev-1's leaf>', text='unblocked: ...')` from `fe-pm`. Only the assignee: anyone else is refused, and the refusal names the assignee. A Main PM never reaches a cell developer (cross-cell): it dms the cell's PM on a task it holds, and that PM dms the developer. The same goes for a PM writing to another cell's member; both refusals name that route.
- **Active-claim required:** otherwise, with an explicit `task_id`, `dm` checks that you are the task's **active claimant** — not just `assigned_to`, which goes stale across a reap/handoff. A reaped or reassigned agent can no longer `dm` about a former task; on `not_authorized` follow its `remediate` (it names the claim verb when the task is still yours to claim).
- **Omitting `task_id`** attaches the message to the one task whose claim you hold. Holding several, or holding none while you have assigned work, is refused with `missing=['task_id']` and the candidate task ids: pass the one the message is about. Dropping `task_id` never gets a message onto a task an explicit `task_id` would be refused on.

## The CEO

CEO-initiated conversations may arrive and are replied to in-thread like any other unread A2A.

## Discover who to message

There is no runtime agent-directory tool. Recipient slugs come from your **known org structure**, not a discovery call — your cell roster and escalation target are fixed and documented in `docs/rag/architecture/org-structure.md` (Cells table) and `docs/rag/architecture/escalation-chain.md`:

```python
dm(recipient="be-qa", text="Anyone hit Y before? Starting task X.")
```

Same-cell peers (e.g. `be-dev-1` alongside `be-dev-2`/`be-qa`/`be-doc`/`be-pm`) are always valid `dm` targets. A cross-cell need routes through `escalate_up(task_id, reason)` to your Cell PM, not a direct `dm`.

## Receive incoming messages — `read_a2a`

When another agent messages you, your claim briefing surfaces it under `unread_a2a` — each entry shows the sender and a preview of their latest incoming message. To read the full bodies (and clear them), call:

```python
read_a2a()  # -> {"messages": [{from_agent, content, created_at}, ...]}
```

`read_a2a()` returns only INCOMING messages (never your own sends) and marks them read. `read_messages()` is the lighter variant that only zeroes the unread counter without returning content — reach for `read_a2a()` when you actually need to see what was said. Either clears `i_am_idle()`'s unread-A2A soft-block.

Formal, ack-required notifications are a separate inbox — see `docs/rag/tools/messaging-tools.md`:

```python
notify_list(unread_only=True)  # list pending items
notify_get(notification_id)  # read one (marks it read)
notify_ack(notification_id)  # acknowledge after handling
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
| Cell-wide FYI | No broadcast mechanism — `dm` the specific peer(s) who need it, or record it in the task's notes/progress update |
