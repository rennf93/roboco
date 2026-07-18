import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProductSummary } from "@/types";

vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => ({
    register: vi.fn(),
    unregister: vi.fn(),
    refresh: vi.fn(),
  }),
}));

const { useProducts } = vi.hoisted(() => ({ useProducts: vi.fn() }));
vi.mock("@/hooks/use-products", () => ({ useProducts }));

vi.mock("../create-product-dialog", () => ({
  CreateProductDialog: () => null,
}));

vi.mock("../product-card-grid", () => ({
  ProductCardGrid: ({ products }: { products?: ProductSummary[] }) => (
    <div data-testid="card-grid">
      {(products ?? []).map((p) => p.name).join(",")}
    </div>
  ),
}));

vi.mock("../product-table", () => ({
  ProductTable: ({ products }: { products?: ProductSummary[] }) => (
    <div data-testid="table">
      {(products ?? []).map((p) => p.name).join(",")}
    </div>
  ),
}));

import { ProductsView, sortProducts } from "../products-view";
import { useUIStore } from "@/store/ui-store";

const PRODUCTS: ProductSummary[] = [
  {
    id: "p-zeta",
    name: "Zeta",
    slug: "zeta",
    cell_count: 1,
    cells: [],
    progress: { done: 0, active: 0, blocked: 0 },
  },
  {
    id: "p-alpha",
    name: "Alpha",
    slug: "alpha",
    cell_count: 3,
    cells: [],
    progress: { done: 0, active: 0, blocked: 0 },
  },
];

describe("ProductsView", () => {
  beforeEach(() => {
    useUIStore.setState({ productsView: "cards" });
    useProducts.mockReturnValue({
      data: PRODUCTS,
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    });
  });

  it("defaults to the card grid view", () => {
    render(<ProductsView />);
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
    expect(screen.queryByTestId("table")).not.toBeInTheDocument();
  });

  it("switches to the table view and back via the toggle", async () => {
    const user = userEvent.setup();
    render(<ProductsView />);

    await user.click(screen.getByLabelText("Table view"));
    expect(screen.getByTestId("table")).toBeInTheDocument();
    expect(screen.queryByTestId("card-grid")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Card view"));
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
    expect(screen.queryByTestId("table")).not.toBeInTheDocument();
  });

  it("sorts cards by name ascending by default", () => {
    render(<ProductsView />);
    expect(screen.getByTestId("card-grid")).toHaveTextContent("Alpha,Zeta");
  });

  it("flips to descending when the direction toggle is clicked", async () => {
    const user = userEvent.setup();
    render(<ProductsView />);
    await user.click(screen.getByLabelText("Toggle sort direction"));
    expect(screen.getByTestId("card-grid")).toHaveTextContent("Zeta,Alpha");
  });
});

describe("sortProducts (pure)", () => {
  const prod = (name: string, cell_count: number) =>
    ({ name, cell_count }) as unknown as ProductSummary;

  it("desc preserves the relative order of ties", () => {
    const rows = [prod("A", 2), prod("B", 2), prod("C", 2)];
    const out = sortProducts(rows, "cells", "desc");
    expect(out.map((r) => r.name)).toEqual(["A", "B", "C"]);
  });
});
