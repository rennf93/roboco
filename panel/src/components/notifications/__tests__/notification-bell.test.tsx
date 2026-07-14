import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationBell } from "../notification-bell";
import { NotificationType, NotificationPriority, type Notification } from "@/types";

// tooltip-aria-label-spec.md §1a: the bell button's only visible content is
// an icon — it needs a mandatory aria-label, plus a matching visible Tooltip
// using the identical string per §2.

const { useNotificationStream, useNotifications, useMarkNotificationRead, useAcknowledgeNotification, useMarkAllNotificationsRead } =
  vi.hoisted(() => ({
    useNotificationStream: vi.fn(),
    useNotifications: vi.fn(),
    useMarkNotificationRead: vi.fn(),
    useAcknowledgeNotification: vi.fn(),
    useMarkAllNotificationsRead: vi.fn(),
  }));

vi.mock("@/hooks/use-websocket", () => ({
  useNotificationStream,
}));

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications,
  useMarkNotificationRead,
  useAcknowledgeNotification,
  useMarkAllNotificationsRead,
  notificationKeys: { all: ["notifications"], list: (f: unknown) => ["notifications", "list", f], detail: (id: string) => ["notifications", "detail", id] },
}));

function buildNotification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: "notif-1",
    type: NotificationType.TASK_ASSIGNMENT,
    priority: NotificationPriority.NORMAL,
    from_agent: "fe-pm-00000000",
    to_agents: ["fe-dev-1"],
    subject: "New task assigned",
    body: "You have been assigned a new task.",
    requires_ack: false,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: null,
    related_message_ids: [],
    timestamp: "2026-07-11T09:00:00Z",
    expires_at: null,
    acked_by: [],
    acked_at: {},
    ...overrides,
  };
}

function streamMock() {
  useNotificationStream.mockReturnValue({
    notifications: [],
    lastMessage: undefined,
    allMessages: [],
    clearMessages: vi.fn(),
    isConnected: true,
    isConnecting: false,
    state: "open",
  });
}

describe("NotificationBell — aria-label + tooltip (tooltip-aria-label-spec §1a)", () => {
  beforeEach(() => {
    streamMock();
    useNotifications.mockReturnValue({ data: undefined });
    useMarkNotificationRead.mockReturnValue({ mutateAsync: vi.fn() });
    useAcknowledgeNotification.mockReturnValue({ mutateAsync: vi.fn() });
    useMarkAllNotificationsRead.mockReturnValue({ mutateAsync: vi.fn() });
  });

  it("exposes 'View notifications' as the bell button's accessible name and title", () => {
    render(<NotificationBell />);
    const button = screen.getByRole("button", { name: "View notifications" });
    expect(button).toHaveAttribute("title", "View notifications");
  });

  it("shows a matching visible tooltip once hovered", async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.hover(screen.getByRole("button", { name: "View notifications" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("View notifications");
  });
});

describe("NotificationBell — read/ack integration (W9-1)", () => {
  beforeEach(() => {
    streamMock();
    useMarkNotificationRead.mockReturnValue({ mutateAsync: vi.fn() });
    useAcknowledgeNotification.mockReturnValue({ mutateAsync: vi.fn() });
    useMarkAllNotificationsRead.mockReturnValue({ mutateAsync: vi.fn() });
  });

  it("shows the persisted unread_count as the badge (not the stream buffer)", () => {
    useNotifications.mockReturnValue({
      data: {
        items: [buildNotification()],
        total: 1,
        unread_count: 3,
        pending_ack_count: 0,
      },
    });
    render(<NotificationBell />);
    // The badge is the only "3" in the closed popover.
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("caps the badge at 9+ for large counts", () => {
    useNotifications.mockReturnValue({
      data: { items: [], total: 42, unread_count: 42, pending_ack_count: 0 },
    });
    render(<NotificationBell />);
    expect(screen.getByText("9+")).toBeInTheDocument();
  });

  it("renders no badge when there is nothing unread", () => {
    useNotifications.mockReturnValue({
      data: { items: [], total: 0, unread_count: 0, pending_ack_count: 0 },
    });
    render(<NotificationBell />);
    expect(screen.queryByText(/^[0-9]/)).not.toBeInTheDocument();
  });

  it("marks a notification read when its 'Mark Read' button is clicked", async () => {
    const markRead = vi.fn().mockResolvedValue(undefined);
    useMarkNotificationRead.mockReturnValue({ mutateAsync: markRead });
    useNotifications.mockReturnValue({
      data: {
        items: [buildNotification({ id: "notif-1", is_read: false })],
        total: 1,
        unread_count: 1,
        pending_ack_count: 0,
      },
    });
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole("button", { name: "View notifications" }));
    const markReadBtn = await screen.findByRole("button", { name: /Mark Read/ });
    await user.click(markReadBtn);
    await waitFor(() => expect(markRead).toHaveBeenCalledWith("notif-1"));
  });

  it("acknowledges a notification when its 'Acknowledge' button is clicked", async () => {
    const ack = vi.fn().mockResolvedValue(buildNotification({ is_acknowledged: true }));
    useAcknowledgeNotification.mockReturnValue({ mutateAsync: ack });
    useNotifications.mockReturnValue({
      data: {
        items: [
          buildNotification({
            id: "notif-2",
            requires_ack: true,
            is_acknowledged: false,
          }),
        ],
        total: 1,
        unread_count: 1,
        pending_ack_count: 1,
      },
    });
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole("button", { name: "View notifications" }));
    const ackBtn = await screen.findByRole("button", { name: /Acknowledge/ });
    await user.click(ackBtn);
    await waitFor(() => expect(ack).toHaveBeenCalledWith("notif-2"));
  });

  it("marks all notifications read via the header action", async () => {
    const markAllRead = vi.fn().mockResolvedValue(undefined);
    useMarkAllNotificationsRead.mockReturnValue({ mutateAsync: markAllRead });
    useNotifications.mockReturnValue({
      data: {
        items: [buildNotification({ is_read: false })],
        total: 1,
        unread_count: 1,
        pending_ack_count: 0,
      },
    });
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole("button", { name: "View notifications" }));
    const allBtn = await screen.findByRole("button", { name: /Mark all read/ });
    await user.click(allBtn);
    await waitFor(() => expect(markAllRead).toHaveBeenCalled());
  });
});