import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { PageRefreshProvider } from "@/components/providers";
import type {
  AdminConversationSummary,
  AdminPairSummary,
  A2AChatMessage,
} from "@/lib/api/a2a";

const {
  useA2AConversations,
  useA2AMessages,
  useA2AAdminPairs,
  useA2ALiveStream,
  invalidateQueries,
  a2aLiveKeys,
} = vi.hoisted(() => ({
  useA2AConversations: vi.fn(),
  useA2AMessages: vi.fn(),
  useA2AAdminPairs: vi.fn(),
  useA2ALiveStream: vi.fn(),
  invalidateQueries: vi.fn(),
  a2aLiveKeys: {
    all: ["a2a-live"] as const,
    conversations: ["a2a-live", "conversations"] as const,
    pairs: ["a2a-live", "pairs"] as const,
    messages: (conversationId: string) =>
      ["a2a-live", "messages", conversationId] as const,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("conversation=conv-1"),
}));

vi.mock("@/hooks/use-a2a-live", () => ({
  a2aLiveKeys,
  useA2AConversations,
  useA2AMessages,
  useA2AAdminPairs,
  useReplyAsCeo: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/hooks/use-websocket", () => ({
  useA2ALiveStream,
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQueryClient: vi.fn(() => ({ invalidateQueries })),
  };
});

vi.mock("@/components/ui/markdown", () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import A2APage from "../page";

function withPageRefresh(ui: ReactNode) {
  return <PageRefreshProvider>{ui}</PageRefreshProvider>;
}

function buildConversation(
  overrides: Partial<AdminConversationSummary> = {},
): AdminConversationSummary {
  return {
    id: "conv-1",
    agent_a: "be-dev-1",
    agent_b: "be-qa",
    topic: "QA handoff",
    task_id: "11111111-2222-3333-4444-555555555555",
    status: "active",
    message_count: 2,
    last_message_at: "2026-07-02T09:00:00Z",
    last_message_preview: "preview",
    created_at: "2026-07-01T08:00:00Z",
    updated_at: "2026-07-02T09:00:00Z",
    ...overrides,
  };
}

function buildPair(
  overrides: Partial<AdminPairSummary> = {},
): AdminPairSummary {
  return {
    agent_a: "be-dev-1",
    role_a: "developer",
    team_a: "backend",
    agent_b: "be-qa",
    role_b: "qa",
    team_b: "backend",
    group_key: "cell-backend",
    conversation_id: "conv-1",
    last_message_at: "2026-07-02T09:00:00Z",
    message_count: 2,
    ...overrides,
  };
}

function buildMessage(): A2AChatMessage {
  return {
    id: "m1",
    conversation_id: "conv-1",
    from_agent: "be-qa",
    content: "transcript body text",
    message_kind: "text",
    response_to_id: null,
    requires_response: false,
    read_at: null,
    created_at: "2026-07-02T09:00:00Z",
    edited_at: null,
  };
}

describe("A2APage", () => {
  beforeEach(() => {
    invalidateQueries.mockReset();
    useA2AConversations.mockReturnValue({
      data: { items: [buildConversation()], total: 1 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    useA2AMessages.mockReturnValue({
      data: { items: [buildMessage()], total: 1, has_more: false },
      isLoading: false,
      refetch: vi.fn(),
    });
    useA2AAdminPairs.mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      refetch: vi.fn(),
    });
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: true,
    });
  });

  it("shows the transcript pane and composer for a task-linked conversation", () => {
    render(withPageRefresh(<A2APage />));
    expect(screen.getByText("transcript body text")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/chime in/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /posts into this conversation — visible to both participants/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("keeps the composer for a closed but task-linked conversation", () => {
    // The watched conversation's status must NOT gate the composer — the reply
    // lands in the CEO's own direct thread with the participant.
    useA2AConversations.mockReturnValue({
      data: { items: [buildConversation({ status: "closed" })], total: 1 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(screen.getByPlaceholderText(/chime in/i)).toBeInTheDocument();
  });

  it("hides the composer and explains why for a task-less conversation", () => {
    // task_id === null is the authoritative signal that the backend's reply
    // route would 400 (replies require a task link), so the pane is read-only.
    useA2AConversations.mockReturnValue({
      data: { items: [buildConversation({ task_id: null })], total: 1 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(screen.queryByPlaceholderText(/chime in/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(/no linked task, so a reply can't be sent/i),
    ).toBeInTheDocument();
  });

  it("invalidates conversations + pairs + selected messages on a matching a2a.message frame", () => {
    useA2ALiveStream.mockReturnValue({
      lastMessage: {
        type: "a2a.message",
        conversation_id: "conv-1",
        message_id: "m9",
        from_agent: "be-dev-1",
        to_agent: "be-qa",
        body_excerpt: "capped",
        timestamp: "2026-07-02T10:00:00Z",
      },
      a2aMessages: [],
      isConnected: true,
    });
    render(withPageRefresh(<A2APage />));
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.conversations,
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.pairs,
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.messages("conv-1"),
    });
  });

  it("only invalidates the conversation + pair lists for frames of other conversations", () => {
    useA2ALiveStream.mockReturnValue({
      lastMessage: {
        type: "a2a.message",
        conversation_id: "conv-other",
        timestamp: "2026-07-02T10:00:00Z",
      },
      a2aMessages: [],
      isConnected: false,
    });
    render(withPageRefresh(<A2APage />));
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.conversations,
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.pairs,
    });
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.messages("conv-1"),
    });
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("shows the switchboard by default with pair cards grouped into sections", () => {
    useA2AAdminPairs.mockReturnValue({
      data: { items: [buildPair()], total: 1 },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(screen.getByText("Switchboard")).toBeInTheDocument();
    expect(screen.getByText(/Backend Cell/)).toBeInTheDocument();
  });

  it("toggles to the classic conversation list and back", () => {
    useA2AAdminPairs.mockReturnValue({
      data: { items: [buildPair()], total: 1 },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(screen.getByText("Switchboard")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Classic conversation list"));
    expect(screen.getByText("Conversations")).toBeInTheDocument();
    expect(screen.queryByText(/Backend Cell/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Switchboard: org-chart pair cards"));
    expect(screen.getByText("Switchboard")).toBeInTheDocument();
  });

  // M44: on /ws/system reconnect (isConnected false → true) the A2A list is
  // stale (events missed during the disconnect); invalidate the a2a query
  // family so react-query refetches.
  it("invalidates a2a queries on a false → true reconnect transition", () => {
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: false,
    });
    const { rerender } = render(withPageRefresh(<A2APage />));
    // No invalidation while offline.
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.all,
    });

    invalidateQueries.mockReset();
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: true,
    });
    act(() => {
      rerender(withPageRefresh(<A2APage />));
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.all,
    });
  });

  it("does not invalidate a2a queries on initial mount when already connected", () => {
    // prevConnected starts unknown; a mount with isConnected=true must NOT
    // fire a reconnect invalidation (only a real false → true transition does).
    invalidateQueries.mockReset();
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: true,
    });
    render(withPageRefresh(<A2APage />));
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.all,
    });
  });

  it("renders the filter bar above the switchboard/list content", () => {
    useA2AAdminPairs.mockReturnValue({
      data: { items: [buildPair()], total: 1 },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(
      screen.getByPlaceholderText("Search agent or topic..."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Active" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
  });

  it("narrows the switchboard's pairs by search", () => {
    useA2AAdminPairs.mockReturnValue({
      data: {
        items: [
          buildPair(),
          buildPair({
            agent_a: "auditor",
            agent_b: "product-owner",
            group_key: "board",
            conversation_id: null,
            last_message_at: null,
            message_count: 0,
          }),
        ],
        total: 2,
      },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    expect(screen.getByText(/Backend Cell/)).toBeInTheDocument();
    expect(screen.getByText(/^Board$/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search agent or topic..."), {
      target: { value: "auditor" },
    });
    expect(screen.queryByText(/Backend Cell/)).not.toBeInTheDocument();
    expect(screen.getByText(/^Board$/)).toBeInTheDocument();
  });

  it("narrows the classic list's conversations by search", () => {
    useA2AConversations.mockReturnValue({
      data: {
        items: [
          buildConversation(),
          buildConversation({
            id: "conv-2",
            agent_a: "ux-dev-1",
            agent_b: "ux-qa",
            topic: "Design review",
          }),
        ],
        total: 2,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2APage />));
    fireEvent.click(screen.getByTitle("Classic conversation list"));
    expect(screen.getByText("QA handoff")).toBeInTheDocument();
    expect(screen.getByText("Design review")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search agent or topic..."), {
      target: { value: "design review" },
    });
    expect(screen.queryByText("QA handoff")).not.toBeInTheDocument();
    expect(screen.getByText("Design review")).toBeInTheDocument();
  });
});
