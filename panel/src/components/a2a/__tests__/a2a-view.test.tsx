import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { PageRefreshProvider } from "@/components/providers";
import type {
  AdminConversationSummary,
  AdminPairSummary,
  A2AChatMessage,
} from "@/lib/api/a2a";

// Extracted from the standalone /a2a page (now the Agents hub's
// Conversations tab, see agents/page.tsx) — pure lift, same suite, new
// import, plus a new describe block for the `?dm=` quick-action handshake.

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

const mockPush = vi.fn();
const mockReplace = vi.fn();
// Stable objects (like real next/navigation — useRouter()'s return value and
// useSearchParams()'s per-URL value only change reference on an actual
// navigation, not on every render), same idiom as
// workstation/__tests__/page.test.tsx's `searchParams` variable. An unstable
// mock here would make the dm-param effect (deps: [dmParam, router,
// searchParams]) refire every render regardless of the real URL.
const mockRouter = { push: mockPush, replace: mockReplace };
let searchParams = new URLSearchParams("conversation=conv-1");
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => searchParams,
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgentDefinitions: () => ({
    data: [
      {
        id: "be-dev-1",
        name: "Backend Dev 1",
        role: "developer",
        team: "backend",
      },
    ],
  }),
}));

vi.mock("@/hooks/use-a2a-live", () => ({
  a2aLiveKeys,
  useA2AConversations,
  useA2AMessages,
  useA2AAdminPairs,
  useReplyAsCeo: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateCeoConversation: () => ({ mutate: vi.fn(), isPending: false }),
  useSendCeoMessage: () => ({ mutate: vi.fn(), isPending: false }),
}));

// AgentSelector (inside A2ANewDmDialog) pulls in useAgentDefinitions + Radix
// Select — irrelevant to this suite, stub it out like create-task-dialog's
// suite does for the same component.
vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: () => null,
}));

vi.mock("@/hooks/use-websocket", () => ({
  useA2ALiveStream,
}));

// The xl:+ context pane's linked-task summary fetches via useTask — stub it
// so this suite doesn't need a real QueryClientProvider.
vi.mock("@/hooks/use-tasks", () => ({
  useTask: () => ({ data: undefined, isLoading: false }),
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

import { A2AView } from "../a2a-view";

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

describe("A2AView", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("conversation=conv-1");
    mockPush.mockClear();
    mockReplace.mockClear();
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
      state: "connected",
    });
  });

  it("polls the transcript + list as an unconditional backstop, tighter once the socket is known-down", () => {
    // Connected (beforeEach default): relaxed 20s backstop — the WS still
    // delivers instantly when healthy, but a silent-dead socket can't freeze
    // the thread for more than one interval.
    render(withPageRefresh(<A2AView />));
    expect(useA2AMessages).toHaveBeenCalledWith(expect.anything(), {
      refetchInterval: 20_000,
    });
    expect(useA2AConversations).toHaveBeenCalledWith(100, true, 20_000);

    // Known-down: tighten to 8s for faster recovery.
    useA2AMessages.mockClear();
    useA2AConversations.mockClear();
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: false,
      state: "disconnected",
    });
    render(withPageRefresh(<A2AView />));
    expect(useA2AMessages).toHaveBeenCalledWith(expect.anything(), {
      refetchInterval: 8_000,
    });
    expect(useA2AConversations).toHaveBeenCalledWith(100, true, 8_000);
  });

  it("shows the transcript pane and composer for a task-linked conversation", () => {
    render(withPageRefresh(<A2AView />));
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
    render(withPageRefresh(<A2AView />));
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
    render(withPageRefresh(<A2AView />));
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
      state: "connected",
    });
    render(withPageRefresh(<A2AView />));
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
      state: "disconnected",
    });
    render(withPageRefresh(<A2AView />));
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
    render(withPageRefresh(<A2AView />));
    expect(screen.getByText("Switchboard")).toBeInTheDocument();
    expect(screen.getByText(/Backend Cell/)).toBeInTheDocument();
  });

  it("toggles to the classic conversation list and back", () => {
    useA2AAdminPairs.mockReturnValue({
      data: { items: [buildPair()], total: 1 },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2AView />));
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
      state: "disconnected",
    });
    const { rerender } = render(withPageRefresh(<A2AView />));
    // No invalidation while offline.
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.all,
    });

    invalidateQueries.mockReset();
    useA2ALiveStream.mockReturnValue({
      lastMessage: null,
      a2aMessages: [],
      isConnected: true,
      state: "connected",
    });
    act(() => {
      rerender(withPageRefresh(<A2AView />));
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
      state: "connected",
    });
    render(withPageRefresh(<A2AView />));
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: a2aLiveKeys.all,
    });
  });

  it("renders the filter trigger above the switchboard/list content", () => {
    useA2AAdminPairs.mockReturnValue({
      data: { items: [buildPair()], total: 1 },
      isLoading: false,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2AView />));
    expect(
      screen.getByRole("button", { name: /^Filters$/ }),
    ).toBeInTheDocument();
  });

  it("narrows the switchboard's pairs by a selected agent", async () => {
    const user = userEvent.setup();
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
    render(withPageRefresh(<A2AView />));
    expect(screen.getByText(/Backend Cell/)).toBeInTheDocument();
    expect(screen.getByText(/^Board$/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.click(screen.getByRole("checkbox", { name: "Auditor" }));

    expect(screen.queryByText(/Backend Cell/)).not.toBeInTheDocument();
    expect(screen.getByText(/^Board$/)).toBeInTheDocument();
  });

  it("narrows the classic list's conversations by a selected agent", async () => {
    const user = userEvent.setup();
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
    render(withPageRefresh(<A2AView />));
    fireEvent.click(screen.getByTitle("Classic conversation list"));
    expect(screen.getByText("QA handoff")).toBeInTheDocument();
    expect(screen.getByText("Design review")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.click(screen.getByRole("checkbox", { name: "UX/UI Dev 1" }));

    expect(screen.queryByText("QA handoff")).not.toBeInTheDocument();
    expect(screen.getByText("Design review")).toBeInTheDocument();
  });

  it("renders the New DM trigger in the header", () => {
    render(withPageRefresh(<A2AView />));
    expect(screen.getByRole("button", { name: /new dm/i })).toBeInTheDocument();
  });

  it("uses the direct composer (no task required) for a CEO-owned conversation", () => {
    // A CEO-initiated DM has no task link and no picker — it must render
    // A2ADirectComposer, not the task-gated A2AReplyComposer.
    useA2AConversations.mockReturnValue({
      data: {
        items: [
          buildConversation({
            agent_a: "ceo",
            agent_b: "be-dev-1",
            task_id: null,
          }),
        ],
        total: 1,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2AView />));
    expect(screen.getByPlaceholderText(/message\.\.\./i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/chime in/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/no linked task, so a reply can't be sent/i),
    ).not.toBeInTheDocument();
  });

  it("narrows the classic list's conversations by task id fragment", async () => {
    const user = userEvent.setup();
    useA2AConversations.mockReturnValue({
      data: {
        items: [
          buildConversation({
            task_id: "11111111-2222-3333-4444-555555555555",
          }),
          buildConversation({
            id: "conv-2",
            topic: "Design review",
            task_id: "99999999-8888-7777-6666-555555555555",
          }),
        ],
        total: 2,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    render(withPageRefresh(<A2AView />));
    fireEvent.click(screen.getByTitle("Classic conversation list"));
    expect(screen.getByText("QA handoff")).toBeInTheDocument();
    expect(screen.getByText("Design review")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.type(screen.getByLabelText("Task id fragment"), "11111111");

    expect(screen.getByText("QA handoff")).toBeInTheDocument();
    expect(screen.queryByText("Design review")).not.toBeInTheDocument();
  });

  // DM quick-action deep link: the agent card's DM button lands here as
  // `?tab=conversations&dm=<agent slug>`.
  describe("`?dm=` quick-action handshake", () => {
    it("opens the New DM dialog and strips the dm param, keeping the rest of the query string", () => {
      searchParams = new URLSearchParams("tab=conversations&dm=be-dev-1");
      render(withPageRefresh(<A2AView />));

      expect(screen.getByText("New direct message")).toBeInTheDocument();
      expect(mockReplace).toHaveBeenCalledTimes(1);
      expect(mockReplace).toHaveBeenCalledWith("/agents?tab=conversations", {
        scroll: false,
      });
    });

    it("does not open the dialog when no dm param is present", () => {
      render(withPageRefresh(<A2AView />));
      expect(screen.queryByText("New direct message")).not.toBeInTheDocument();
      expect(mockReplace).not.toHaveBeenCalled();
    });

    it("re-arms for a repeated identical dm value after the strip (latch reset)", () => {
      searchParams = new URLSearchParams("tab=conversations&dm=be-dev-1");
      const { rerender } = render(withPageRefresh(<A2AView />));
      expect(mockReplace).toHaveBeenCalledTimes(1);

      // The strip landed: same mounted view, dm gone from the URL.
      searchParams = new URLSearchParams("tab=conversations");
      rerender(withPageRefresh(<A2AView />));

      // The SAME dm value arrives again (re-pasted link / second click
      // without a tab remount) — the handshake must fire again.
      searchParams = new URLSearchParams("tab=conversations&dm=be-dev-1");
      rerender(withPageRefresh(<A2AView />));
      expect(mockReplace).toHaveBeenCalledTimes(2);
    });

    it("drops to a bare /agents path when dm was the only param", () => {
      searchParams = new URLSearchParams("dm=be-dev-1");
      render(withPageRefresh(<A2AView />));
      expect(mockReplace).toHaveBeenCalledWith("/agents", { scroll: false });
    });
  });
});
