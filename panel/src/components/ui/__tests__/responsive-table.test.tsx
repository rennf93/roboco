import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "../responsive-table";

// Deterministic matchMedia stub — jsdom has none. `matches` is controlled
// per-test via the module-level flag so useIsMobile resolves synchronously
// within the component's mount effect.
let mockMatches = false;
beforeEach(() => {
  mockMatches = false;
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: mockMatches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
});

describe("ResponsiveTable", () => {
  it("renders the desktop table branch when the viewport does not match mobile", () => {
    mockMatches = false;
    render(
      <ResponsiveTable
        table={<div data-testid="desktop-table">table</div>}
        cards={<div data-testid="mobile-cards">cards</div>}
      />,
    );
    expect(screen.getByTestId("desktop-table")).toBeInTheDocument();
    expect(screen.queryByTestId("mobile-cards")).not.toBeInTheDocument();
  });

  it("renders only the card branch below the breakpoint — never both at once", () => {
    mockMatches = true;
    render(
      <ResponsiveTable
        table={<div data-testid="desktop-table">table</div>}
        cards={<div data-testid="mobile-cards">cards</div>}
      />,
    );
    expect(screen.getByTestId("mobile-cards")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-table")).not.toBeInTheDocument();
  });
});

describe("ResponsiveTableCard building blocks", () => {
  it("renders a card with labeled rows", () => {
    render(
      <ResponsiveTableCardList>
        <ResponsiveTableCard>
          <ResponsiveTableCardRow label="Status">Active</ResponsiveTableCardRow>
        </ResponsiveTableCard>
      </ResponsiveTableCardList>,
    );
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });
});
