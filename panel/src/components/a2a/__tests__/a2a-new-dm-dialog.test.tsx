import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import React from "react";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/hooks/use-a2a-live", () => ({
  useCreateCeoConversation: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// AgentSelector pulls in useAgentDefinitions (react-query) + Radix Select —
// stub it as a plain input so this suite can drive `onChange` directly,
// mirroring the create-task-dialog test idiom for the same component.
vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: ({
    value,
    onChange,
  }: {
    value: string | null;
    onChange: (v: string | null) => void;
  }) => (
    <input
      aria-label="Agent"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
    />
  ),
}));

import { A2ANewDmDialog } from "../a2a-new-dm-dialog";

function openDialog() {
  render(<A2ANewDmDialog onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /new dm/i }));
}

describe("A2ANewDmDialog", () => {
  beforeEach(() => {
    mutate.mockReset();
  });

  it("disables Start conversation until an agent is picked and a message is typed", () => {
    openDialog();
    const submit = screen.getByRole("button", { name: /start conversation/i });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Agent"), {
      target: { value: "be-dev-1" },
    });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/what do you want to say/i), {
      target: { value: "Status update please" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("submits { target_agent, initial_message } on Start conversation", () => {
    openDialog();
    fireEvent.change(screen.getByLabelText("Agent"), {
      target: { value: "be-dev-1" },
    });
    fireEvent.change(screen.getByPlaceholderText(/what do you want to say/i), {
      target: { value: "Status update please" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start conversation/i }));

    expect(mutate).toHaveBeenCalledWith(
      { target_agent: "be-dev-1", initial_message: "Status update please" },
      expect.anything(),
    );
  });

  it("calls onCreated with the new conversation id and closes on success", () => {
    const onCreated = vi.fn();
    render(<A2ANewDmDialog onCreated={onCreated} />);
    fireEvent.click(screen.getByRole("button", { name: /new dm/i }));
    fireEvent.change(screen.getByLabelText("Agent"), {
      target: { value: "be-dev-1" },
    });
    fireEvent.change(screen.getByPlaceholderText(/what do you want to say/i), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start conversation/i }));

    const [, callbacks] = mutate.mock.calls[0] as [
      unknown,
      { onSuccess: (c: { id: string }) => void },
    ];
    act(() => callbacks.onSuccess({ id: "conv-new" }));

    expect(onCreated).toHaveBeenCalledWith("conv-new");
    // Dialog closed -> the trigger is the only "New DM" text left, the
    // "Start conversation" button is gone.
    expect(
      screen.queryByRole("button", { name: /start conversation/i }),
    ).not.toBeInTheDocument();
  });

  describe("controlled open + initialTarget (DM quick-action deep link)", () => {
    it("stays closed by default when open=false, and opens with the target preselected once open=true", () => {
      const onOpenChange = vi.fn();
      const { rerender } = render(
        <A2ANewDmDialog
          onCreated={vi.fn()}
          open={false}
          onOpenChange={onOpenChange}
          initialTarget="be-dev-1"
        />,
      );
      expect(
        screen.queryByRole("button", { name: /start conversation/i }),
      ).not.toBeInTheDocument();

      rerender(
        <A2ANewDmDialog
          onCreated={vi.fn()}
          open={true}
          onOpenChange={onOpenChange}
          initialTarget="be-dev-1"
        />,
      );
      expect(screen.getByLabelText("Agent")).toHaveValue("be-dev-1");
    });

    it("routes trigger clicks and Escape/close through the caller's onOpenChange, not internal state", () => {
      const onOpenChange = vi.fn();
      render(
        <A2ANewDmDialog
          onCreated={vi.fn()}
          open={true}
          onOpenChange={onOpenChange}
          initialTarget={null}
        />,
      );
      // The trigger sits behind Radix's aria-hidden focus-trap boundary
      // while the dialog is open, so it must be queried with hidden: true.
      fireEvent.click(
        screen.getByRole("button", { name: /new dm/i, hidden: true }),
      );
      // Radix requests a state change; the controlled caller decides — this
      // dialog never flips itself open/closed while controlled.
      expect(onOpenChange).toHaveBeenCalled();
    });
  });
});
