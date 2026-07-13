import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useUIStore } from "@/store";

// tooltip-aria-label-spec.md §1a: the collapse-rail toggle previously had no
// accessible name at all. The label must track the current action (collapse
// vs. expand), not a static string, so a screen reader announces what will
// happen next.

vi.mock("next/navigation", () => ({
  usePathname: () => "/overview",
}));

import { Sidebar } from "../sidebar";

describe("Sidebar — collapse toggle aria-label (tooltip-aria-label-spec §1a)", () => {
  it("labels the toggle 'Collapse sidebar' when expanded", () => {
    useUIStore.setState({ sidebarCollapsed: false });
    render(<Sidebar />);

    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle).toHaveAttribute("title", "Collapse sidebar");
  });

  it("flips to 'Expand sidebar' once collapsed", () => {
    useUIStore.setState({ sidebarCollapsed: false });
    render(<Sidebar />);

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(
      screen.getByRole("button", { name: "Expand sidebar" }),
    ).toBeInTheDocument();
  });
});
