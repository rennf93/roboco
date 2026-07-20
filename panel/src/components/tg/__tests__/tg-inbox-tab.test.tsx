import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgInboxTab } from "../tg-inbox-tab";
import { NotificationPriority, NotificationType } from "@/types";

const { ackMock, items } = vi.hoisted(() => ({
  ackMock: vi.fn(),
  items: { current: [] as Array<Record<string, unknown>> },
}));
// No task in the shared index — every UUID falls back to the #id8 handle.
vi.mock("@/hooks/use-tasks", () => ({
  useTasks: () => ({ data: [] }),
}));
vi.mock("@/hooks/use-notifications", () => ({
  notificationKeys: { all: ["notifications"] },
  useNotifications: () => ({
    data: { items: items.current },
    isLoading: false,
  }),
  useAcknowledgeNotification: () => ({ mutate: ackMock, isPending: false }),
}));

function notification(overrides: Record<string, unknown>) {
  return {
    id: "n1",
    type: NotificationType.BROADCAST,
    priority: NotificationPriority.NORMAL,
    from_agent: "main-pm",
    to_agents: ["ceo"],
    subject: "A notification",
    body: "body",
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

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TgInboxTab />
    </QueryClientProvider>,
  );
}

describe("TgInboxTab", () => {
  it("humanizes an unresolved uuid subject to a short id handle", () => {
    items.current = [
      notification({
        id: "n1",
        subject: "Task 123e4567-e89b-12d3-a456-426614174000 needs review",
      }),
    ];
    renderTab();
    expect(screen.getByText(/#123e4567/)).toBeInTheDocument();
  });

  it("splits a bracketed prefix into its own chip", () => {
    items.current = [
      notification({
        id: "n1",
        subject: "[strategy engine] weekly digest ready",
      }),
    ];
    renderTab();
    expect(screen.getByText("Strategy engine")).toBeInTheDocument();
    expect(screen.getByText("Weekly digest ready")).toBeInTheDocument();
  });

  it("shows inbox zero when there is nothing", () => {
    items.current = [];
    renderTab();
    expect(screen.getByText(/inbox zero/i)).toBeInTheDocument();
  });
});
