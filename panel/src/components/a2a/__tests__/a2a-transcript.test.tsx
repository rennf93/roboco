import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { A2AChatMessage } from "@/lib/api/a2a";

// react-markdown is heavyweight and irrelevant here — render bodies as-is.
vi.mock("@/components/ui/markdown", () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

import { A2ATranscript } from "../a2a-transcript";

function buildMessage(overrides: Partial<A2AChatMessage>): A2AChatMessage {
  return {
    id: "m1",
    conversation_id: "conv-1",
    from_agent: "be-dev-1",
    content: "hello",
    message_kind: "text",
    response_to_id: null,
    requires_response: false,
    read_at: null,
    created_at: "2026-07-02T10:00:00Z",
    edited_at: null,
    ...overrides,
  };
}

describe("A2ATranscript", () => {
  it("renders messages chronologically with sender names and timestamps", () => {
    // Deliberately unordered payload: the later message first.
    const { container } = render(
      <A2ATranscript
        messages={[
          buildMessage({
            id: "m2",
            from_agent: "be-qa",
            content: "second message body",
            created_at: "2026-07-02T10:05:00Z",
          }),
          buildMessage({
            id: "m1",
            from_agent: "be-dev-1",
            content: "first message body",
            created_at: "2026-07-02T10:00:00Z",
          }),
        ]}
        isLoading={false}
      />,
    );

    expect(screen.getByText("Backend Dev 1")).toBeInTheDocument();
    expect(screen.getByText("Backend QA")).toBeInTheDocument();
    // Every message carries a relative timestamp.
    expect(screen.getAllByText(/ago$/)).toHaveLength(2);
    // Chronological order: oldest first regardless of payload order.
    const text = container.textContent ?? "";
    expect(text.indexOf("first message body")).toBeLessThan(
      text.indexOf("second message body"),
    );
  });

  it("shows the message kind as an outline badge when present", () => {
    render(
      <A2ATranscript
        messages={[buildMessage({ message_kind: "escalation" })]}
        isLoading={false}
      />,
    );
    expect(screen.getByText("escalation")).toBeInTheDocument();
  });

  it("shows the empty state when there are no messages", () => {
    render(<A2ATranscript messages={[]} isLoading={false} />);
    expect(
      screen.getByText(/No messages in this conversation yet/),
    ).toBeInTheDocument();
  });
});
