import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/hooks/use-a2a-live", () => ({
  useReplyAsCeo: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Make the Select testable without Radix's portal/pointer machinery: each
// SelectItem renders a button carrying its value; clicking it invokes the
// nearest Select's onValueChange (scoped via context).
vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<(v: string) => void>(() => {});
  return {
    Select: ({
      onValueChange,
      children,
    }: {
      onValueChange?: (v: string) => void;
      children: React.ReactNode;
    }) => (
      <Ctx.Provider value={onValueChange ?? (() => {})}>
        {children}
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: React.ReactNode;
    }) => {
      const onValueChange = React.useContext(Ctx);
      return (
        <button data-value={value} onClick={() => onValueChange(value)}>
          {children}
        </button>
      );
    },
  };
});

import { A2AReplyComposer } from "../a2a-reply-composer";

function renderComposer(lastSender: string | null = "be-qa") {
  return render(
    <A2AReplyComposer
      conversationId="conv-1"
      agentA="be-dev-1"
      agentB="be-qa"
      lastSender={lastSender}
    />,
  );
}

describe("A2AReplyComposer", () => {
  beforeEach(() => {
    mutate.mockReset();
  });

  it("disables Send when the textarea is empty", () => {
    renderComposer();
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("sends { to_agent, content } defaulting to the last message's sender", () => {
    renderComposer("be-qa");
    fireEvent.change(screen.getByPlaceholderText(/chime in/i), {
      target: { value: "Ship it" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: "conv-1",
        to_agent: "be-qa",
        content: "Ship it",
      }),
      expect.anything(),
    );
  });

  it("falls back to agent_a when there is no last sender", () => {
    renderComposer(null);
    fireEvent.change(screen.getByPlaceholderText(/chime in/i), {
      target: { value: "Status?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_agent: "be-dev-1", content: "Status?" }),
      expect.anything(),
    );
  });

  it("sends to an explicitly selected participant", () => {
    const { container } = renderComposer("be-qa");
    container
      .querySelector<HTMLButtonElement>('[data-value="be-dev-1"]')
      ?.click();
    fireEvent.change(screen.getByPlaceholderText(/chime in/i), {
      target: { value: "Over to you" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_agent: "be-dev-1" }),
      expect.anything(),
    );
  });

  it("states the interjection semantics honestly in the helper text", () => {
    renderComposer();
    // Guard the honesty note: the message posts into THIS conversation,
    // visible to both participants — not a re-homed CEO<->target DM.
    expect(
      screen.getByText(/posts into this conversation/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/visible to both participants/i),
    ).toBeInTheDocument();
  });
});

describe("A2AReplyComposer when one participant is the CEO", () => {
  beforeEach(() => {
    mutate.mockReset();
  });

  function renderCeoComposer(lastSender: string | null = "be-dev-1") {
    return render(
      <A2AReplyComposer
        conversationId="conv-ceo"
        agentA="ceo"
        agentB="be-dev-1"
        lastSender={lastSender}
      />,
    );
  }

  it("never offers the CEO itself as a recipient", () => {
    const { container } = renderCeoComposer();
    expect(container.querySelector('[data-value="ceo"]')).toBeNull();
    expect(container.querySelector('[data-value="be-dev-1"]')).not.toBeNull();
  });

  it("always sends to the other participant, even when they spoke last as agent_a", () => {
    renderCeoComposer("ceo");
    fireEvent.change(screen.getByPlaceholderText(/chime in/i), {
      target: { value: "Following up" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_agent: "be-dev-1" }),
      expect.anything(),
    );
  });
});
