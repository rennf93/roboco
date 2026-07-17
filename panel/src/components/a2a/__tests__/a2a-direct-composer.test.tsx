import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/hooks/use-a2a-live", () => ({
  useSendCeoMessage: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { A2ADirectComposer } from "../a2a-direct-composer";

function renderComposer() {
  return render(
    <A2ADirectComposer conversationId="conv-ceo" otherAgent="be-dev-1" />,
  );
}

describe("A2ADirectComposer", () => {
  beforeEach(() => {
    mutate.mockReset();
  });

  it("disables Send when the textarea is empty", () => {
    renderComposer();
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("sends { conversationId, content } with no recipient to pick", () => {
    renderComposer();
    fireEvent.change(screen.getByPlaceholderText(/message/i), {
      target: { value: "Following up" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(mutate).toHaveBeenCalledWith(
      { conversationId: "conv-ceo", content: "Following up" },
      expect.anything(),
    );
  });

  it("clears the textarea on a successful send", () => {
    renderComposer();
    const textarea = screen.getByPlaceholderText(/message/i);
    fireEvent.change(textarea, { target: { value: "Following up" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    const [, callbacks] = mutate.mock.calls[0] as [
      unknown,
      { onSuccess: () => void },
    ];
    act(() => callbacks.onSuccess());

    expect((textarea as HTMLTextAreaElement).value).toBe("");
  });

  it("names the direct-thread recipient, not the watched-conversation semantics", () => {
    renderComposer();
    expect(
      screen.getByText(/your own direct thread with backend dev 1/i),
    ).toBeInTheDocument();
  });
});
