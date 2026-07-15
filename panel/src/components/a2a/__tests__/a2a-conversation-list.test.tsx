import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdminConversationSummary } from "@/lib/api/a2a";
import { A2AConversationList } from "../a2a-conversation-list";

// jsdom has no ResizeObserver; Radix ScrollArea only reaches for one once a
// Tooltip portal mounts inside it and triggers a size recalculation — the
// other renders below never hit that path. Stub it for the hover test.
if (typeof window !== "undefined" && !window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
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
    message_count: 7,
    last_message_at: "2026-07-02T09:00:00Z",
    last_message_preview: "Tests are green on the branch.",
    created_at: "2026-07-01T08:00:00Z",
    updated_at: "2026-07-02T09:00:00Z",
    ...overrides,
  };
}

describe("A2AConversationList", () => {
  it("renders participants, relative time, preview, status badge and task chip", () => {
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={vi.fn()}
        isLoading={false}
        pulses={{}}
      />,
    );

    // Participants via getAgentDisplayName ("{a} <-> {b}").
    expect(screen.getByText(/Backend Dev 1/)).toBeInTheDocument();
    expect(screen.getByText(/Backend QA/)).toBeInTheDocument();
    // Both participants get an avatar, matching A2APairCard's PairAvatar
    // (initials + a hover tooltip with the full name — see next test).
    expect(screen.getByText("BD1")).toBeInTheDocument();
    expect(screen.getByText("BQA")).toBeInTheDocument();
    // Topic, preview, message count, relative timestamp.
    expect(screen.getByText("QA handoff")).toBeInTheDocument();
    expect(
      screen.getByText("Tests are green on the branch."),
    ).toBeInTheDocument();
    expect(screen.getByText("7 msgs")).toBeInTheDocument();
    expect(screen.getByText(/ago$/)).toBeInTheDocument();
    // Status badge.
    expect(screen.getByText("active")).toBeInTheDocument();
    // Task chip links to the task page.
    const chip = screen.getByRole("link", { name: /Task 11111111/ });
    expect(chip).toHaveAttribute(
      "href",
      "/tasks/11111111-2222-3333-4444-555555555555",
    );
  });

  it("shows the full name in a hover tooltip on the abbreviated avatar", async () => {
    const user = userEvent.setup();
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={vi.fn()}
        isLoading={false}
        pulses={{}}
      />,
    );
    await user.hover(screen.getByText("BD1"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Backend Dev 1",
    );
  });

  it("fires onSelect with the conversation id on row click", () => {
    const onSelect = vi.fn();
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={onSelect}
        isLoading={false}
        pulses={{}}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("conv-1");
  });

  it("does not hijack row selection when the task chip is clicked", () => {
    const onSelect = vi.fn();
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={onSelect}
        isLoading={false}
        pulses={{}}
      />,
    );
    fireEvent.click(screen.getByRole("link", { name: /Task 11111111/ }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows the empty state when there are no conversations", () => {
    render(
      <A2AConversationList
        conversations={[]}
        selectedId={null}
        onSelect={vi.fn()}
        isLoading={false}
        pulses={{}}
      />,
    );
    expect(screen.getByText(/No A2A conversations yet/)).toBeInTheDocument();
  });

  it("flashes a row hot when its pair's pulse key matches (same key as the switchboard)", () => {
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={vi.fn()}
        isLoading={false}
        pulses={{ "be-dev-1|be-qa": 1700000000000 }}
      />,
    );
    expect(screen.getByTestId("conversation-row")).toHaveAttribute(
      "data-pulsing",
      "true",
    );
  });
});
