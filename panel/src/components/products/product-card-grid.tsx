"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { Boxes, Pencil } from "lucide-react";
import type { ProductSummary } from "@/types";
import { EditProductDialog } from "./edit-product-dialog";
import { CellsList, ProgressCell } from "./product-table";

interface ProductCardGridProps {
  products: ProductSummary[] | undefined;
  isLoading: boolean;
}

// Same intrinsic-sizing grid as the Agents page card grid (agent-grid.tsx):
// one column on a phone, as many as fit at 17rem+ on a wide monitor.
const GRID_COLS = "grid-cols-[repeat(auto-fill,minmax(17rem,1fr))]";

export function ProductCardGrid({ products, isLoading }: ProductCardGridProps) {
  const [editingProductId, setEditingProductId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className={"grid gap-3 " + GRID_COLS}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="gap-2.5 py-4">
            <CardHeader className="gap-1 px-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-16" />
            </CardHeader>
          </Card>
        ))}
      </div>
    );
  }

  if (!products || products.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <Boxes className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p className="text-lg font-medium">No products found</p>
        <p className="text-sm">Create a product to map cells to projects</p>
      </div>
    );
  }

  return (
    <>
      <div className={"grid gap-3 " + GRID_COLS}>
        {products.map((product) => (
          <Card key={product.id} className="gap-2.5 py-4">
            <CardHeader className="gap-1 px-4">
              <div className="flex items-center justify-between gap-1">
                <CardTitle className="min-w-0 truncate text-base">
                  <Button
                    onClick={() => setEditingProductId(product.id)}
                    variant="link"
                    className="h-auto max-w-full truncate p-0 font-semibold text-base text-foreground"
                  >
                    {product.name}
                  </Button>
                </CardTitle>
                <HelpTip label="Edit product name, description, and cell-project mapping">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 shrink-0"
                    onClick={() => setEditingProductId(product.id)}
                    aria-label="Edit product"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </HelpTip>
              </div>
              <HelpTip label="Identifier for this cell-to-project grouping, used to reference it across the panel and API.">
                <p className="w-fit truncate text-xs text-muted-foreground font-mono">
                  {product.slug}
                </p>
              </HelpTip>
            </CardHeader>
            <CardContent className="px-4 space-y-2.5">
              <CellsList cells={product.cells} />
              <ProgressCell progress={product.progress} />
            </CardContent>
          </Card>
        ))}
      </div>

      {editingProductId && (
        <EditProductDialog
          productId={editingProductId}
          open={!!editingProductId}
          onOpenChange={(open) => {
            if (!open) setEditingProductId(null);
          }}
        />
      )}
    </>
  );
}
