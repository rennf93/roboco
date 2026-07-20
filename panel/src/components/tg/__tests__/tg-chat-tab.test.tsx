import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgChatTab } from "../tg-chat-tab";

const { mineItems, fleetItems, messages, sendMock, replyMock, markReadMock } =
  vi.hoisted(() => ({
    mineItems: { current: [] as Array<Record<string, unknown>> },
    fleetItems: { current: [] as Array<Record<string, unknown>> },
    messages: { current: [] as Array<Record<string, unknown>> },
    sendMock: vi.fn(),
    replyMock: vi.fn(),
    markReadMock: vi.fn(),
  }));

vi.mock("@/hooks/use-a2a-live", () => ({
  a2aLiveKeys: {
    all: ["a2a-live"],
    conversations: ["a2a-live", "conversations"],
    ceoConversations: ["a2a-live", "ceo-conversations"],
    pairs: ["a2a-live", "pairs"],
    messages: (id: string) => ["a2a-live", "messages", id],
  },
  useCeoConversations: () => ({
    data: { items: mineItems.current, total: mineItems.current.length },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useA2AConversations: () => ({
    data: { items: fleetItems.current, total: fleetItems.current.length },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useA2AMessages: () => ({
    data: {
      items: messages.current,
      total: messages.current.length,
      has_more: false,
    },
    isLoading: false,
  }),
  useSendCeoMessage: () => ({ mutate: sendMock, isPending: false }),
  useReplyAsCeo: () => ({ mutate: replyMock, isPending: false }),
  useCreateCeoConversation: () => ({ mutate: vi.fn(), isPending: false }),
  useMarkConversationRead: () => ({ mutate: markReadMock, isPending: false }),
}));
vi.mock("@/hooks/use-websocket", () => ({
  useA2ALiveStream: () => ({ lastMessage: null, isConnected: true }),
}));
vi.mock("@/hooks/use-tasks", () => ({
  useTasks: () => ({ data: [] }),
}));
vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: () => <div data-testid="agent-selector" />,
}));
vi.mock("@/components/a2a/a2a-new-dm-dialog", () => ({
  EXCLUDE_NON_DM_ROLES: [],
}));

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TgChatTab />
    </QueryClientProvider>,
  );
}

const mineRow = (over: Record<string, unknown> = {}) => ({
  id: "c1",
  other_agent: "main-pm",
  topic: null,
  task_id: null,
  status: "active",
  message_count: 2,
  unread_count: 3,
  last_message_at: new Date().toISOString(),
  last_message_preview:
    "**Wave 2** shipped for 33333333-3333-4333-8333-333333333333",
  ...over,
});

const fleetRow = (over: Record<string, unknown> = {}) => ({
  id: "f1",
  agent_a: "be-dev-1",
  agent_b: "be-qa",
  topic: "QA handoff",
  task_id: "t-1",
  status: "active",
  message_count: 5,
  last_message_at: new Date().toISOString(),
  last_message_preview: "Suite is green.",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...over,
});

const msg = (over: Record<string, unknown> = {}) => ({
  id: `m-${Math.random()}`,
  conversation_id: "c1",
  from_agent: "main-pm",
  content: "Hello **there**",
  message_kind: "text",
  response_to_id: null,
  requires_response: false,
  read_at: null,
  created_at: new Date().toISOString(),
  edited_at: null,
  ...over,
});

beforeEach(() => {
  mineItems.current = [];
  fleetItems.current = [];
  messages.current = [];
  sendMock.mockReset();
  replyMock.mockReset();
  markReadMock.mockReset();
});

describe("TgChatTab — list", () => {
  it("shows the CEO's own threads with unread badge and a groomed preview", () => {
    mineItems.current = [mineRow()];
    renderTab();

    expect(screen.getByText("Main PM")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // Markdown stripped, UUID shortened — never 36 raw chars.
    const preview = screen.getByText(/Wave 2 shipped for #33333333/);
    expect(preview.textContent).not.toContain("**");
    expect(preview.textContent).not.toContain("-3333-");
  });

  it("Fleet scope lists agent↔agent threads and hides CEO pairs", async () => {
    fleetItems.current = [
      fleetRow(),
      fleetRow({ id: "f2", agent_a: "ceo", agent_b: "main-pm" }),
    ];
    renderTab();

    await userEvent.click(screen.getByRole("button", { name: "Fleet" }));
    expect(screen.getByText(/QA handoff/)).toBeInTheDocument();
    // The CEO pair is Mine-only — never duplicated into Fleet.
    expect(screen.queryByText(/Main PM/)).not.toBeInTheDocument();
  });
});

describe("TgChatTab — threads", () => {
  it("opens a Mine thread, renders agent markdown, clears unread", async () => {
    mineItems.current = [mineRow()];
    messages.current = [msg(), msg({ from_agent: "ceo", content: "Thanks" })];
    renderTab();

    await userEvent.click(screen.getByText("Main PM"));
    expect(markReadMock).toHaveBeenCalledWith("c1");
    // Agent message renders markdown (bold survives as <strong>).
    expect(screen.getByText("there").tagName).toBe("STRONG");
    // CEO bubble is plain text.
    expect(screen.getByText("Thanks")).toBeInTheDocument();
  });

  it("sends into a Mine thread via the plain CEO send", async () => {
    mineItems.current = [mineRow()];
    renderTab();

    await userEvent.click(screen.getByText("Main PM"));
    await userEvent.type(screen.getByPlaceholderText("Message…"), "On it");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(sendMock).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: "c1", content: "On it" }),
      expect.anything(),
    );
  });

  it("task-linked Fleet thread interjects via replyAsCeo with a recipient", async () => {
    fleetItems.current = [fleetRow()];
    messages.current = [msg({ conversation_id: "f1", from_agent: "be-dev-1" })];
    renderTab();

    await userEvent.click(screen.getByRole("button", { name: "Fleet" }));
    await userEvent.click(screen.getByText(/QA handoff/));
    // Default recipient = last non-CEO sender.
    const chip = screen.getByRole("button", { name: /tap to switch/i });
    expect(chip.textContent).toContain("Backend Dev 1");

    await userEvent.type(screen.getByPlaceholderText("Message…"), "Status?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(replyMock).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "f1",
        to_agent: "be-dev-1",
        content: "Status?",
      }),
      expect.anything(),
    );
  });

  it("Fleet thread without a task link is watch-only", async () => {
    fleetItems.current = [fleetRow({ task_id: null })];
    renderTab();

    await userEvent.click(screen.getByRole("button", { name: "Fleet" }));
    await userEvent.click(screen.getByText(/QA handoff/));

    expect(screen.getByText(/Watch-only/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Message…")).not.toBeInTheDocument();
  });
});
