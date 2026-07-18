import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { ProductCardGrid } from "../product-card-grid";
import { Team } from "@/types";
import type { ProductSummary } from "@/types";

const product: ProductSummary = {
  id: "p1",
  name: "RoboCo Platform",
  slug: "roboco-platform",
  cell_count: 3,
  cells: [
    { team: Team.BACKEND, project_id: "proj-1", project_name: "roboco" },
    { team: Team.FRONTEND, project_id: "proj-1", project_name: "roboco" },
  ],
  progress: { done: 42, active: 5, blocked: 1 },
};

describe("ProductCardGrid", () => {
  it("renders one card carrying the product's name, cells, and progress", () => {
    render(<ProductCardGrid products={[product]} isLoading={false} />);
    expect(screen.getByText("RoboCo Platform")).toBeInTheDocument();
    expect(screen.getByText("roboco-platform")).toBeInTheDocument();
    expect(screen.getByText("Backend")).toBeInTheDocument();
    expect(screen.getAllByText("roboco").length).toBe(2);
    expect(screen.getByText("42 done")).toBeInTheDocument();
    expect(screen.getByText("1 blocked")).toBeInTheDocument();
  });

  it("shows the empty state when there are no products", () => {
    render(<ProductCardGrid products={[]} isLoading={false} />);
    expect(screen.getByText("No products found")).toBeInTheDocument();
  });

  it("shows the unmapped label for a product with no cells", () => {
    const bare: ProductSummary = {
      id: "p2",
      name: "Bare Product",
      slug: "bare",
      cell_count: 0,
      cells: [],
      progress: { done: 0, active: 0, blocked: 0 },
    };
    render(<ProductCardGrid products={[bare]} isLoading={false} />);
    expect(screen.getByText("Unmapped")).toBeInTheDocument();
  });

  it("renders loading skeletons and not the empty state while loading", () => {
    render(<ProductCardGrid products={undefined} isLoading={true} />);
    expect(screen.queryByText("No products found")).not.toBeInTheDocument();
  });
});
