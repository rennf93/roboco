import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { A2ASwitchboard } from "../a2a-switchboard";

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
    conversation_id: null,
    last_message_at: null,
    message_count: 0,
    ...overrides,
  };
}

describe("A2ASwitchboard", () => {
  it("shows a loading skeleton grid", () => {
    render(
      <A2ASwitchboard
        pairs={[]}
        pulses={{}}
        selectedConversationId={null}
        isLoading
        onOpenPair={vi.fn()}
      />,
    );
    // No section headings or empty-state copy while loading.
    expect(screen.queryByText(/Backend Cell/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No allowed A2A pairs configured/),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state when there are no pairs", () => {
    render(
      <A2ASwitchboard
        pairs={[]}
        pulses={{}}
        selectedConversationId={null}
        isLoading={false}
        onOpenPair={vi.fn()}
      />,
    );
    expect(
      screen.getByText("No allowed A2A pairs configured"),
    ).toBeInTheDocument();
  });

  it("groups pairs into labeled sections with counts", () => {
    const pairs = [
      buildPair({ group_key: "cell-backend" }),
      buildPair({
        group_key: "cell-backend",
        agent_a: "be-dev-2",
        agent_b: "be-doc",
      }),
      buildPair({
        group_key: "board",
        agent_a: "auditor",
        agent_b: "product-owner",
      }),
    ];
    render(
      <A2ASwitchboard
        pairs={pairs}
        pulses={{}}
        selectedConversationId={null}
        isLoading={false}
        onOpenPair={vi.fn()}
      />,
    );
    expect(screen.getByText(/Backend Cell/)).toBeInTheDocument();
    expect(screen.getByText("(2)")).toBeInTheDocument();
    expect(screen.getByText(/^Board$/)).toBeInTheDocument();
    expect(screen.getByText("(1)")).toBeInTheDocument();
    expect(screen.getAllByTestId("pair-card")).toHaveLength(3);
  });

  it("calls onOpenPair with the clicked pair", () => {
    const onOpenPair = vi.fn();
    const pair = buildPair({ conversation_id: "conv-9" });
    render(
      <A2ASwitchboard
        pairs={[pair]}
        pulses={{}}
        selectedConversationId={null}
        isLoading={false}
        onOpenPair={onOpenPair}
      />,
    );
    fireEvent.click(screen.getByTestId("pair-card"));
    expect(onOpenPair).toHaveBeenCalledWith(pair);
  });

  it("marks the card matching selectedConversationId as selected", () => {
    const pair = buildPair({ conversation_id: "conv-9" });
    render(
      <A2ASwitchboard
        pairs={[pair]}
        pulses={{}}
        selectedConversationId="conv-9"
        isLoading={false}
        onOpenPair={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("collapses and re-expands a section when its header is clicked", () => {
    render(
      <A2ASwitchboard
        pairs={[buildPair()]}
        pulses={{}}
        selectedConversationId={null}
        isLoading={false}
        onOpenPair={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Backend Cell/ }));
    expect(screen.queryByTestId("pair-card")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Backend Cell/ }));
    expect(screen.getByTestId("pair-card")).toBeInTheDocument();
  });

  it("passes each pair's pulse timestamp through by canonical pair key", () => {
    const pair = buildPair();
    render(
      <A2ASwitchboard
        pairs={[pair]}
        pulses={{ "be-dev-1|be-qa": 1700000000000 }}
        selectedConversationId={null}
        isLoading={false}
        onOpenPair={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "true",
    );
  });
});
