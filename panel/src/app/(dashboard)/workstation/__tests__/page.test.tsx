import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The two tab panes have their own dedicated tests — stub them here so this
// page test only checks tab composition + the URL-driven default, mirroring
// social/__tests__/page.test.tsx.
vi.mock("@/components/products/products-view", () => ({
  ProductsView: () => <div>ProductsViewStub</div>,
}));
vi.mock("@/components/projects/projects-view", () => ({
  ProjectsView: () => <div>ProjectsViewStub</div>,
}));

const mockReplace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => searchParams,
}));

import WorkstationPage from "../page";

describe("WorkstationPage", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams();
    mockReplace.mockClear();
  });

  it("defaults to the Products tab when the URL carries no ?tab", () => {
    render(<WorkstationPage />);

    expect(screen.getByRole("tab", { name: "Products" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Projects" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByText("ProductsViewStub")).toBeInTheDocument();
  });

  it("activates the Projects tab from ?tab=projects", () => {
    searchParams = new URLSearchParams("tab=projects");
    render(<WorkstationPage />);

    expect(screen.getByRole("tab", { name: "Projects" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Products" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByText("ProjectsViewStub")).toBeInTheDocument();
  });
});
