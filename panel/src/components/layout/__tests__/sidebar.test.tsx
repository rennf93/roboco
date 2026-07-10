import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SidebarNav, navItems } from "../sidebar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/overview",
}));

describe("SidebarNav", () => {
  it("renders a divider between each of the six nav groups", () => {
    const { container } = render(<SidebarNav />);
    // One less divider than the number of groups (none before the first group).
    expect(container.querySelectorAll('[data-slot="separator"]')).toHaveLength(
      5,
    );
  });

  it("renders the /a2a entry labeled 'A2A', not 'A2A Live'", () => {
    render(<SidebarNav />);
    const link = screen.getByRole("link", { name: "A2A" });
    expect(link).toHaveAttribute("href", "/a2a");
    expect(screen.queryByText("A2A Live")).not.toBeInTheDocument();
  });

  it("no longer renders a Notifications entry", () => {
    render(<SidebarNav />);
    expect(screen.queryByRole("link", { name: /notifications/i })).toBeNull();
    expect(navItems.some((item) => item.href === "/notifications")).toBe(
      false,
    );
  });

  it("keeps the same relative item order as before", () => {
    expect(navItems.map((item) => item.href)).toEqual([
      "/overview",
      "/business",
      "/social",
      "/tasks",
      "/kanban",
      "/prompter",
      "/projects",
      "/products",
      "/git",
      "/agents",
      "/knowledge-base",
      "/auditor",
      "/a2a",
      "/journals",
      "/metrics",
    ]);
  });

  it("renders correctly when collapsed (icon-only, no layout break)", () => {
    const { container } = render(<SidebarNav collapsed />);
    expect(
      container.querySelectorAll('[data-slot="separator"]'),
    ).toHaveLength(5);
    // Labels are hidden, but the links/icons still render.
    expect(screen.getAllByRole("link")).toHaveLength(navItems.length);
    expect(screen.queryByText("A2A")).not.toBeInTheDocument();
  });
});
