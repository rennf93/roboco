import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { PageRefreshProvider } from "@/components/providers";
import { NotificationType, NotificationPriority, type Notification } from "@/types";

const {
  useNotifications,
  useMarkNotificationRead,
  useAcknowledgeNotification,
  useMarkAllNotificationsRead,
} = vi.hoisted(() => ({
  useNotifications: vi.fn(),
  useMarkNotificationRead: vi.fn(),
  useAcknowledgeNotification: vi.fn(),
  useMarkAllNotificationsRead: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("tab=all"),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications,
  useMarkNotificationRead,
  useAcknowledgeNotification,
  useMarkAllNotificationsRead,
}));

vi.mock("@/components/ui/markdown", () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import NotificationsPage from "../page";

function withPageRefresh(ui: ReactNode) {
  return <PageRefreshProvider>{ui}</PageRefreshProvider>;
}

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
    related_task_id: "11111111-2222-3333-4444-555555555555",
    related_message_ids: [],
    timestamp: "2026-07-11T09:00:00Z",
    expires_at: null,
    acked_by: [],
    acked_at: {},
    ...overrides,
  };
}

describe("NotificationsPage", () => {
  beforeEach(() => {
    useMarkNotificationRead.mockReturnValue({ mutateAsync: vi.fn() });
    useAcknowledgeNotification.mockReturnValue({ mutateAsync: vi.fn() });
    useMarkAllNotificationsRead.mockReturnValue({ mutateAsync: vi.fn() });
  });

  it("renders a TASK_ASSIGNMENT notification with a working deep-link to its task", () => {
    useNotifications.mockReturnValue({
      data: {
        items: [buildNotification()],
        total: 1,
        unread_count: 1,
        pending_ack_count: 0,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(withPageRefresh(<NotificationsPage />));

    expect(screen.getByText("New task assigned")).toBeInTheDocument();

    const taskLink = screen.getByRole("link", { name: /Task #11111111/i });
    expect(taskLink).toHaveAttribute(
      "href",
      "/tasks/11111111-2222-3333-4444-555555555555",
    );
  });

  it("renders each of the 5 coordination-event notification types with a distinguishing icon", () => {
    const types = [
      NotificationType.TASK_ASSIGNMENT,
      NotificationType.BLOCKER_ESCALATION,
      NotificationType.REVIEW_REQUEST,
      NotificationType.DOCUMENTATION_REQUEST,
      NotificationType.APPROVAL,
    ];
    const items = types.map((type, idx) =>
      buildNotification({
        id: `notif-${idx}`,
        type,
        subject: `Subject for ${type}`,
        related_task_id: null,
      }),
    );

    useNotifications.mockReturnValue({
      data: { items, total: items.length, unread_count: 0, pending_ack_count: 0 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(withPageRefresh(<NotificationsPage />));

    for (const type of types) {
      expect(screen.getByText(`Subject for ${type}`)).toBeInTheDocument();
    }
  });
});
