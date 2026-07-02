import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { AdminConversationSummary } from "@/lib/api/a2a";
import { A2AConversationList } from "../a2a-conversation-list";

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
      />,
    );

    // Participants via getAgentDisplayName ("{a} <-> {b}").
    expect(screen.getByText(/Backend Dev 1/)).toBeInTheDocument();
    expect(screen.getByText(/Backend QA/)).toBeInTheDocument();
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

  it("fires onSelect with the conversation id on row click", () => {
    const onSelect = vi.fn();
    render(
      <A2AConversationList
        conversations={[buildConversation()]}
        selectedId={null}
        onSelect={onSelect}
        isLoading={false}
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
      />,
    );
    expect(screen.getByText(/No A2A conversations yet/)).toBeInTheDocument();
  });
});
