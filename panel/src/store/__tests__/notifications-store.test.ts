import { describe, it, expect, beforeEach } from "vitest";
import { useNotificationStore } from "@/store/notifications-store";
import { NotificationType, NotificationPriority } from "@/types";
import type { Notification } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _idCounter = 0;

function makeNotification(overrides: Partial<Notification> = {}): Notification {
  _idCounter += 1;
  return {
    id: `notif-${_idCounter}`,
    type: NotificationType.ALERT,
    priority: NotificationPriority.NORMAL,
    from_agent: "be-dev-1",
    to_agents: ["ceo"],
    subject: "Test notification",
    body: "Test body",
    requires_ack: false,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: null,
    related_message_ids: [],
    timestamp: new Date().toISOString(),
    expires_at: null,
    acked_by: [],
    acked_at: {},
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Reset store before every test to avoid state leakage
// ---------------------------------------------------------------------------

beforeEach(() => {
  _idCounter = 0;
  useNotificationStore.setState({
    notifications: [],
    unreadCount: 0,
    pendingAckCount: 0,
  });
});

// ---------------------------------------------------------------------------
// addNotification
// ---------------------------------------------------------------------------

describe("useNotificationStore — addNotification", () => {
  it("adds the notification to the store", () => {
    const n = makeNotification();
    useNotificationStore.getState().addNotification(n);
    expect(useNotificationStore.getState().notifications).toContainEqual(n);
  });

  it("increments unreadCount when the notification is unread", () => {
    useNotificationStore
      .getState()
      .addNotification(makeNotification({ is_read: false }));
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });

  it("does NOT increment unreadCount when the notification is already read", () => {
    useNotificationStore
      .getState()
      .addNotification(makeNotification({ is_read: true }));
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it("increments pendingAckCount when requires_ack and not yet acknowledged", () => {
    useNotificationStore
      .getState()
      .addNotification(
        makeNotification({ requires_ack: true, is_acknowledged: false }),
      );
    expect(useNotificationStore.getState().pendingAckCount).toBe(1);
  });

  it("does NOT increment pendingAckCount when requires_ack is false", () => {
    useNotificationStore
      .getState()
      .addNotification(makeNotification({ requires_ack: false }));
    expect(useNotificationStore.getState().pendingAckCount).toBe(0);
  });

  it("does NOT increment pendingAckCount when notification is already acknowledged", () => {
    useNotificationStore
      .getState()
      .addNotification(
        makeNotification({ requires_ack: true, is_acknowledged: true }),
      );
    expect(useNotificationStore.getState().pendingAckCount).toBe(0);
  });

  it("deduplicates: re-delivering the same id updates in-place without incrementing counters", () => {
    const n = makeNotification({ is_read: false });
    const store = useNotificationStore.getState();

    // First delivery
    store.addNotification(n);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
    expect(useNotificationStore.getState().notifications).toHaveLength(1);

    // Second delivery of the SAME id (with updated fields)
    const updated = { ...n, subject: "Updated subject" };
    useNotificationStore.getState().addNotification(updated);

    // Counter must NOT have been double-counted
    expect(useNotificationStore.getState().unreadCount).toBe(1);
    // Still only one entry in the list
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    // The entry was updated in place
    expect(useNotificationStore.getState().notifications[0].subject).toBe(
      "Updated subject",
    );
  });

  it("prepends new notifications (newest first)", () => {
    const first = makeNotification();
    const second = makeNotification();
    useNotificationStore.getState().addNotification(first);
    useNotificationStore.getState().addNotification(second);
    expect(useNotificationStore.getState().notifications[0].id).toBe(second.id);
    expect(useNotificationStore.getState().notifications[1].id).toBe(first.id);
  });
});

// ---------------------------------------------------------------------------
// markAsRead
// ---------------------------------------------------------------------------

describe("useNotificationStore — markAsRead", () => {
  it("marks the target notification as read", () => {
    const n = makeNotification({ is_read: false });
    useNotificationStore.getState().addNotification(n);
    useNotificationStore.getState().markAsRead(n.id);
    const updated = useNotificationStore
      .getState()
      .notifications.find((x) => x.id === n.id);
    expect(updated?.is_read).toBe(true);
  });

  it("decrements unreadCount when marking a notification read", () => {
    const n = makeNotification({ is_read: false });
    useNotificationStore.getState().addNotification(n);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
    useNotificationStore.getState().markAsRead(n.id);
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it("unreadCount never drops below 0", () => {
    // markAsRead on an empty store shouldn't produce negative count
    useNotificationStore.setState({ unreadCount: 0 });
    useNotificationStore.getState().markAsRead("non-existent-id");
    expect(useNotificationStore.getState().unreadCount).toBeGreaterThanOrEqual(
      0,
    );
  });

  it("does not affect other notifications", () => {
    const n1 = makeNotification({ is_read: false });
    const n2 = makeNotification({ is_read: false });
    useNotificationStore.getState().addNotification(n1);
    useNotificationStore.getState().addNotification(n2);
    useNotificationStore.getState().markAsRead(n1.id);
    const n2Updated = useNotificationStore
      .getState()
      .notifications.find((x) => x.id === n2.id);
    expect(n2Updated?.is_read).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// markAsAcknowledged
// ---------------------------------------------------------------------------

describe("useNotificationStore — markAsAcknowledged", () => {
  it("marks the target notification as acknowledged", () => {
    const n = makeNotification({
      requires_ack: true,
      is_acknowledged: false,
    });
    useNotificationStore.getState().addNotification(n);
    useNotificationStore.getState().markAsAcknowledged(n.id);
    const updated = useNotificationStore
      .getState()
      .notifications.find((x) => x.id === n.id);
    expect(updated?.is_acknowledged).toBe(true);
  });

  it("decrements pendingAckCount when acknowledging a notification", () => {
    const n = makeNotification({
      requires_ack: true,
      is_acknowledged: false,
    });
    useNotificationStore.getState().addNotification(n);
    expect(useNotificationStore.getState().pendingAckCount).toBe(1);
    useNotificationStore.getState().markAsAcknowledged(n.id);
    expect(useNotificationStore.getState().pendingAckCount).toBe(0);
  });

  it("pendingAckCount never drops below 0", () => {
    useNotificationStore.setState({ pendingAckCount: 0 });
    useNotificationStore.getState().markAsAcknowledged("non-existent-id");
    expect(
      useNotificationStore.getState().pendingAckCount,
    ).toBeGreaterThanOrEqual(0);
  });
});

// ---------------------------------------------------------------------------
// setCounts
// ---------------------------------------------------------------------------

describe("useNotificationStore — setCounts", () => {
  it("sets unreadCount and pendingAckCount directly", () => {
    useNotificationStore.getState().setCounts(7, 3);
    expect(useNotificationStore.getState().unreadCount).toBe(7);
    expect(useNotificationStore.getState().pendingAckCount).toBe(3);
  });

  it("overrides previous counter values", () => {
    useNotificationStore.setState({ unreadCount: 10, pendingAckCount: 5 });
    useNotificationStore.getState().setCounts(2, 1);
    expect(useNotificationStore.getState().unreadCount).toBe(2);
    expect(useNotificationStore.getState().pendingAckCount).toBe(1);
  });

  it("accepts zero for both counters", () => {
    useNotificationStore.setState({ unreadCount: 4, pendingAckCount: 2 });
    useNotificationStore.getState().setCounts(0, 0);
    expect(useNotificationStore.getState().unreadCount).toBe(0);
    expect(useNotificationStore.getState().pendingAckCount).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// clearAll
// ---------------------------------------------------------------------------

describe("useNotificationStore — clearAll", () => {
  it("empties the notifications array", () => {
    useNotificationStore.getState().addNotification(makeNotification());
    useNotificationStore.getState().addNotification(makeNotification());
    useNotificationStore.getState().clearAll();
    expect(useNotificationStore.getState().notifications).toEqual([]);
  });

  it("resets unreadCount to 0", () => {
    useNotificationStore
      .getState()
      .addNotification(makeNotification({ is_read: false }));
    useNotificationStore.getState().clearAll();
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it("resets pendingAckCount to 0", () => {
    useNotificationStore
      .getState()
      .addNotification(
        makeNotification({ requires_ack: true, is_acknowledged: false }),
      );
    useNotificationStore.getState().clearAll();
    expect(useNotificationStore.getState().pendingAckCount).toBe(0);
  });
});
