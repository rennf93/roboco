import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

// Mutable pathname shared with the hoisted mock factory.
const { pathnameMock } = vi.hoisted(() => ({ pathnameMock: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => pathnameMock() }));

import { SidebarFooter } from "../sidebar";

function linkFor(container: HTMLElement, href: string) {
  return container.querySelector<HTMLAnchorElement>(`a[href="${href}"]`);
}

describe("SidebarFooter active state (exact match)", () => {
  beforeEach(() => pathnameMock.mockReset());

  it("highlights only the matching footer link on /settings", () => {
    pathnameMock.mockReturnValue("/settings");
    const { container } = render(<SidebarFooter />);
    expect(linkFor(container, "/settings")?.className).toMatch(/bg-primary/);
    expect(linkFor(container, "/settings/ai-providers")?.className).not.toMatch(
      /bg-primary/,
    );
    expect(linkFor(container, "/business")?.className).not.toMatch(/bg-primary/);
  });

  it("does not double-highlight /settings when on /settings/ai-providers", () => {
    pathnameMock.mockReturnValue("/settings/ai-providers");
    const { container } = render(<SidebarFooter />);
    expect(linkFor(container, "/settings/ai-providers")?.className).toMatch(
      /bg-primary/,
    );
    expect(linkFor(container, "/settings")?.className).not.toMatch(/bg-primary/);
  });
});