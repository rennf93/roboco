import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The three tab panes have their own dedicated tests — stub them here so
// this page test only checks tab composition + the URL-driven default,
// mirroring workstation/__tests__/page.test.tsx.
vi.mock("@/components/agents/agents-fleet-view", () => ({
  AgentsFleetView: () => <div>AgentsFleetViewStub</div>,
}));
vi.mock("@/components/a2a/a2a-view", () => ({
  A2AView: () => <div>A2AViewStub</div>,
}));
vi.mock("@/components/journals/journals-view", () => ({
  JournalsView: () => <div>JournalsViewStub</div>,
}));

const mockReplace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => searchParams,
}));

import AgentsPage from "../page";

describe("AgentsPage", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams();
    mockReplace.mockClear();
  });

  it("defaults to the Fleet tab when the URL carries no ?tab", () => {
    render(<AgentsPage />);

    expect(screen.getByRole("tab", { name: "Fleet" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByText("AgentsFleetViewStub")).toBeInTheDocument();
  });

  it("activates the Conversations tab from ?tab=conversations", () => {
    searchParams = new URLSearchParams("tab=conversations");
    render(<AgentsPage />);

    expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Fleet" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByText("A2AViewStub")).toBeInTheDocument();
  });

  it("activates the Journals tab from ?tab=journals", () => {
    searchParams = new URLSearchParams("tab=journals");
    render(<AgentsPage />);

    expect(screen.getByRole("tab", { name: "Journals" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Fleet" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByText("JournalsViewStub")).toBeInTheDocument();
  });
});
