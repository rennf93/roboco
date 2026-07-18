"use client";

import { useEffect, useMemo, useState } from "react";
import { useProducts } from "@/hooks/use-products";
import { useUIStore } from "@/store/ui-store";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProductDialog } from "@/components/products/create-product-dialog";
import { ProductCardGrid } from "@/components/products/product-card-grid";
import { ProductTable } from "@/components/products/product-table";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpTip } from "@/components/ui/help-tip";
import { ArrowDown, ArrowUp, LayoutGrid, Table2 } from "lucide-react";
import { usePageRefresh } from "@/hooks";
import type { ProductSummary } from "@/types";

type ProductSortKey = "name" | "cells";
type SortDirection = "asc" | "desc";

const SORT_OPTIONS: { value: ProductSortKey; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "cells", label: "Cell count" },
];

// Exported for direct unit tests. Multiplier, not sort-then-reverse — see
// sortProjects in projects-view.tsx.
export function sortProducts(
  products: ProductSummary[],
  key: ProductSortKey,
  direction: SortDirection,
): ProductSummary[] {
  const dir = direction === "asc" ? 1 : -1;
  return [...products].sort(
    (a, b) =>
      dir *
      (key === "name"
        ? a.name.localeCompare(b.name)
        : a.cell_count - b.cell_count),
  );
}

/** Products tab content — extracted from the standalone /products page so it
 * can live inside the Workstation tab shell (see workstation/page.tsx). */
export function ProductsView() {
  const { data: products, isLoading, error, refetch } = useProducts();
  const view = useUIStore((s) => s.productsView);
  const setView = useUIStore((s) => s.setProductsView);
  const [sortKey, setSortKey] = useState<ProductSortKey>("name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  // Sorting is client-side over the already-loaded list — only relevant to
  // the card view; the table keeps its own (unsorted, creation-order) render.
  const sortedProducts = useMemo(
    () => (products ? sortProducts(products, sortKey, sortDirection) : products),
    [products, sortKey, sortDirection],
  );

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Products</h1>
          <p className="text-muted-foreground">
            Map each cell to the project it works on for a product
          </p>
        </div>
        <div className="flex items-center gap-2">
          {view === "cards" && (
            <>
              <Select
                value={sortKey}
                onValueChange={(v) => setSortKey(v as ProductSortKey)}
              >
                <HelpTip label="Sort the product cards by this field">
                  <SelectTrigger size="sm" className="w-auto min-w-32">
                    <SelectValue />
                  </SelectTrigger>
                </HelpTip>
                <SelectContent>
                  {SORT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <HelpTip
                label={
                  sortDirection === "asc"
                    ? "Ascending — click for descending"
                    : "Descending — click for ascending"
                }
              >
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() =>
                    setSortDirection((d) => (d === "asc" ? "desc" : "asc"))
                  }
                  aria-label="Toggle sort direction"
                >
                  {sortDirection === "asc" ? (
                    <ArrowUp className="h-4 w-4" />
                  ) : (
                    <ArrowDown className="h-4 w-4" />
                  )}
                </Button>
              </HelpTip>
            </>
          )}
          <div className="flex items-center gap-1 rounded-md border p-0.5">
            <HelpTip label="Card view — boxes with badges, one per product">
              <Button
                type="button"
                variant={view === "cards" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                aria-pressed={view === "cards"}
                aria-label="Card view"
                onClick={() => setView("cards")}
              >
                <LayoutGrid className="h-3.5 w-3.5" />
              </Button>
            </HelpTip>
            <HelpTip label="Table view">
              <Button
                type="button"
                variant={view === "table" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                aria-pressed={view === "table"}
                aria-label="Table view"
                onClick={() => setView("table")}
              >
                <Table2 className="h-3.5 w-3.5" />
              </Button>
            </HelpTip>
          </div>
          <CreateProductDialog />
        </div>
      </div>

      {/* Content */}
      {isOffline ? (
        <OfflineState
          title="Cannot Load Products"
          description="Start the RoboCo orchestrator to manage products. Products map cells to the projects they work on."
          onRetry={() => void refresh()}
        />
      ) : view === "cards" ? (
        <ProductCardGrid products={sortedProducts} isLoading={isLoading} />
      ) : (
        <ProductTable products={products} isLoading={isLoading} />
      )}
    </div>
  );
}
