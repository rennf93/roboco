# Learning notification in-memory queue (bounded)

Source: `roboco/services/learning.py` (`LearningPropagationService`), PR #1119.

## How notifications are written

Each recorded learning notifies its recipients twice:

1. **Durable path**: `_persist_notifications` bulk-inserts `NotificationTable` rows. This is the record the notifications API and `NotificationDeliveryService` read and acknowledge.
2. **In-memory path**: `_enqueue_in_memory` appends one `LearningNotification` per notified agent to `self._notification_queue` in the orchestrator process.

Recipient selection (`_fetch_notify_agents`: TEAM by role, CELL by the author's team, ORG all non-human agents) is unchanged.

## Behavior change: the queue is bounded

Before, `_notification_queue` was an unbounded `list`. Nothing in production calls `get_pending_notifications` or `acknowledge_notification` (the API acks through `NotificationDeliveryService`), so entries were never removed and the queue grew until the orchestrator restarted.

Now:

- `_notification_queue` is a `collections.deque` with `maxlen=_NOTIFICATION_QUEUE_MAXLEN` (**256**, a module constant in `learning.py`).
- Appending past the cap silently **evicts the oldest entries**. This is safe: the `NotificationTable` rows remain the durable record, and no production code reads the in-memory copy.
- `acknowledge_notification(notification_id, agent_id)` also **prunes every acknowledged entry** from the queue (the deque is rebuilt with the same `maxlen`), so acknowledged entries stop occupying slots. Because nothing acks in production, the cap, not the prune, is what bounds growth.

## Unchanged

- Public signatures of `get_pending_notifications` and `acknowledge_notification`. "Pending" still means unacknowledged entries for the requesting agent.
- No DB migration, no API change.

## Caveats

- The in-memory queue is not a delivery record. It holds at most the 256 most recent entries and is lost on restart; use the DB notifications.
- The queue still exists. Removal was deferred because integration tests read `svc._notification_queue`.
- Unit coverage in `tests/unit/services/test_learning.py`: eviction at the cap, prune-on-ack, pending filtering.
- `docs/map/*` is generated and was not hand-edited; it picks the change up on regeneration.
