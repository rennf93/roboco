import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SidebarNav, SidebarFooter, navItems } from "../sidebar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/overview",
}));

const EXPECTED_ORDER = [
  "/overview",
  "/prompter",
  "/tasks",
  "/kanban",
  "/git",
  "/projects",
  "/products",
  "/social",
  "/knowledge-base",
  "/a2a",
  "/agents",
  "/journals",
  "/auditor",
  "/metrics",
];

describe("navItems", () => {
  it("is a single flat array in the exact expected order", () => {
    expect(navItems.map((item) => item.href)).toEqual(EXPECTED_ORDER);
  });

  it("does not include Business", () => {
    expect(navItems.some((item) => item.href === "/business")).toBe(false);
  });
});

describe("SidebarNav", () => {
  it("renders no dividers — the nav list itself has no group separators", () => {
    const { container } = render(<SidebarNav />);
    expect(
      container.querySelectorAll('[data-slot="separator"]'),
    ).toHaveLength(0);
  });

  it("renders every nav item as a link, in order", () => {
    render(<SidebarNav />);
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual(
      EXPECTED_ORDER,
    );
  });

  it("renders correctly when collapsed (icon-only, no layout break)", () => {
    render(<SidebarNav collapsed />);
    expect(screen.getAllByRole("link")).toHaveLength(navItems.length);
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
  });
});

describe("SidebarFooter", () => {
  it("renders Business immediately before AI Providers, with Settings last", () => {
    render(<SidebarFooter />);
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/business",
      "/settings/ai-providers",
      "/settings",
    ]);
  });

  it("renders exactly one Separator between the nav list and the footer, expanded and collapsed", () => {
    const expanded = render(<SidebarFooter />);
    expect(
      expanded.container.querySelectorAll('[data-slot="separator"]'),
    ).toHaveLength(1);
    expanded.unmount();

    const collapsed = render(<SidebarFooter collapsed />);
    expect(
      collapsed.container.querySelectorAll('[data-slot="separator"]'),
    ).toHaveLength(1);
  });

  it("renders correctly when collapsed (icon-only)", () => {
    render(<SidebarFooter collapsed />);
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.queryByText("Business")).not.toBeInTheDocument();
  });
});
