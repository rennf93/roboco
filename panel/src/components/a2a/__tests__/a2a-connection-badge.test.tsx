import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ConnectionState } from "@/lib/websocket/connection";
import {
  A2AConnectionBadge,
  A2AConnectionBanner,
} from "../a2a-connection-badge";

describe("A2AConnectionBadge", () => {
  it.each([
    ["connected", "Live"],
    ["connecting", "Connecting…"],
    ["reconnecting", "Reconnecting…"],
    ["disconnected", "Offline"],
  ] satisfies [ConnectionState, string][])(
    "renders a distinct label for %s",
    (state, label) => {
      render(<A2AConnectionBadge state={state} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );
});

describe("A2AConnectionBanner", () => {
  it("reads 'Reconnecting' for the reconnecting state", () => {
    render(<A2AConnectionBanner state="reconnecting" onDismiss={vi.fn()} />);
    expect(
      screen.getByText(/Reconnecting — messages may be out of date/),
    ).toBeInTheDocument();
  });

  it("reads 'Disconnected' for the disconnected state", () => {
    render(<A2AConnectionBanner state="disconnected" onDismiss={vi.fn()} />);
    expect(
      screen.getByText(/Disconnected — reconnecting automatically/),
    ).toBeInTheDocument();
  });

  it("dismiss button fires onDismiss", () => {
    const onDismiss = vi.fn();
    render(<A2AConnectionBanner state="disconnected" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("shows a matching visible tooltip on the dismiss button", async () => {
    const user = userEvent.setup();
    render(<A2AConnectionBanner state="disconnected" onDismiss={vi.fn()} />);
    await user.hover(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Dismiss");
  });
});
