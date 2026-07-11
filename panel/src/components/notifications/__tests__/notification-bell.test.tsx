import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NotificationBell } from "../notification-bell";

// tooltip-aria-label-spec.md §1a: the bell button's only visible content is
// an icon — it needs a mandatory aria-label, plus a matching visible
// Tooltip using the identical string per §2.

vi.mock("@/hooks/use-websocket", () => ({
  useNotificationStream: () => ({
    notifications: [],
    isConnected: false,
    isConnecting: false,
    clearMessages: () => {},
  }),
}));

describe("NotificationBell — aria-label + tooltip (tooltip-aria-label-spec §1a)", () => {
  it("exposes 'View notifications' as the bell button's accessible name and title", () => {
    render(<NotificationBell />);
    const button = screen.getByRole("button", { name: "View notifications" });
    expect(button).toHaveAttribute("title", "View notifications");
  });

  it("shows a matching visible tooltip once hovered", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<NotificationBell />);

    await user.hover(
      screen.getByRole("button", { name: "View notifications" }),
    );

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "View notifications",
    );
  });
});
