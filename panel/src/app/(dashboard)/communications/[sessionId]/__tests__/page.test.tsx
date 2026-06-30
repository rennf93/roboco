import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Session } from "@/types";

// Bundle D / defect 3: posting to a CLOSED session silently redirects the
// message to a fresh active session, so it vanishes from the closed-session
// view the user is looking at. The page must guard the composer when the
// session is not active and tell the user why.

const { useSession, useSessionMessages, messageKeys, sessionKeys } = vi.hoisted(
  () => ({
    useSession: vi.fn(),
    useSessionMessages: vi.fn(),
    messageKeys: { list: (id: string) => ["messages", "list", id] },
    sessionKeys: { detail: (id: string) => ["sessions", "detail", id] },
  }),
);

vi.mock("next/navigation", () => ({
  useParams: () => ({ sessionId: "s1" }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/hooks/use-channels", () => ({
  useSession,
  useSessionMessages,
  messageKeys,
  sessionKeys,
}));

vi.mock("@/hooks/use-websocket", () => ({
  useSessionStream: () => ({ lastMessage: null }),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useMutation: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
    useQueryClient: vi.fn(() => ({ invalidateQueries: vi.fn() })),
  };
});

vi.mock("@/lib/api/messages", () => ({ messagesApi: { send: vi.fn() } }));

import SessionDetailPage from "../page";

function buildSession(status: string): Session {
  return {
    id: "s1",
    group_id: "g1",
    status: status as never,
    scope: "cell" as never,
    message_count: 2,
    total_content_length: 10,
    started_at: "2026-06-30T00:00:00Z",
    last_activity_at: "2026-06-30T00:00:00Z",
    closed_at: status === "closed" ? "2026-06-30T01:00:00Z" : null,
    task_links: [],
  };
}

describe("SessionDetailPage — closed-session composer guard", () => {
  beforeEach(() => {
    useSession.mockReset();
    useSessionMessages.mockReset();
    useSessionMessages.mockReturnValue({
      data: { items: [] },
      isLoading: false,
      refetch: vi.fn(),
    });
  });

  it("shows a closed-session notice instead of the composer when closed", () => {
    useSession.mockReturnValue({
      data: buildSession("closed"),
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<SessionDetailPage />);
    expect(screen.getByText(/session is closed/i)).toBeInTheDocument();
    // The message textarea must not be available for a closed session.
    expect(
      screen.queryByPlaceholderText(/type a message/i),
    ).not.toBeInTheDocument();
  });

  it("shows the composer for an active session", () => {
    useSession.mockReturnValue({
      data: buildSession("active"),
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<SessionDetailPage />);
    expect(screen.getByPlaceholderText(/type a message/i)).toBeInTheDocument();
  });
});
