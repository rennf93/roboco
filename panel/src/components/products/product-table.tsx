"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "@/components/ui/responsive-table";
import { Skeleton } from "@/components/ui/skeleton";
import { Boxes, Pencil } from "lucide-react";
import type { ProductSummary, Team } from "@/types";
import { EditProductDialog } from "./edit-product-dialog";
import { HelpTip } from "@/components/ui/help-tip";

const TEAM_LABELS: Record<Team, string> = {
  board: "Board",
  main_pm: "Main PM",
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
  marketing: "Marketing",
};

interface ProductTableProps {
  products: ProductSummary[] | undefined;
  isLoading: boolean;
}

function CellsList({ cells }: { cells: ProductSummary["cells"] }) {
  if (cells.length === 0) {
    return <span className="text-muted-foreground text-sm">Unmapped</span>;
  }
  return (
    <HelpTip label="Each cell (Backend/Frontend/UX-UI) works on the project listed beside it. A cell with no project mapped here does no work for this product.">
      <div className="flex flex-col gap-1">
        {cells.map((c) => (
          <div key={`${c.team}-${c.project_id}`} className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="bg-blue-500/10 text-blue-500 text-xs"
            >
              {TEAM_LABELS[c.team] ?? c.team}
            </Badge>
            <span className="text-muted-foreground text-xs truncate">
              {c.project_name || "—"}
            </span>
          </div>
        ))}
      </div>
    </HelpTip>
  );
}

function ProgressCell({
  progress,
}: {
  progress: ProductSummary["progress"];
}) {
  const { done, active, blocked } = progress;
  const atRisk = blocked > 0;
  const dotHint = atRisk
    ? "At risk — this product's mapped projects have blocked tasks needing attention."
    : done > 0
      ? "Healthy — tasks are completing with none currently blocked."
      : "No completed or blocked tasks yet.";
  return (
    <div className="flex items-center gap-2">
      <HelpTip label={dotHint}>
        <span
          className={
            "h-2 w-2 rounded-full inline-block " +
            (atRisk ? "bg-amber-500" : done > 0 ? "bg-emerald-500" : "bg-muted")
          }
        />
      </HelpTip>
      <div className="flex items-center gap-2 text-xs">
        <span className="text-emerald-600 dark:text-emerald-400">{done} done</span>
        <span className="text-muted-foreground">{active} active</span>
        {blocked > 0 && (
          <span className="text-amber-600 dark:text-amber-400">{blocked} blocked</span>
        )}
      </div>
    </div>
  );
}

export function ProductTable({ products, isLoading }: ProductTableProps) {
  const [editingProductId, setEditingProductId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
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
      <ResponsiveTable
        table={
          <div className="border rounded-lg">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Cells</TableHead>
                  <TableHead>Progress</TableHead>
                  <TableHead className="w-[100px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {products.map((product) => (
                  <TableRow key={product.id}>
                    <TableCell>
                      <div>
                        <Button
                          onClick={() => setEditingProductId(product.id)}
                          variant="link"
                          className="h-auto p-0 font-medium text-foreground"
                        >
                          {product.name}
                        </Button>
                        <p className="text-xs text-muted-foreground font-mono">
                          {product.slug}
                        </p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <CellsList cells={product.cells} />
                    </TableCell>
                    <TableCell>
                      <ProgressCell progress={product.progress} />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <HelpTip label="Edit product name, description, and cell-project mapping">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setEditingProductId(product.id)}
                            aria-label="Edit product"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                        </HelpTip>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        }
        cards={
          <ResponsiveTableCardList>
            {products.map((product) => (
              <ResponsiveTableCard key={product.id}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <Button
                      onClick={() => setEditingProductId(product.id)}
                      variant="link"
                      className="h-auto p-0 font-medium text-foreground"
                    >
                      {product.name}
                    </Button>
                    <p className="text-xs text-muted-foreground font-mono">
                      {product.slug}
                    </p>
                  </div>
                  <HelpTip label="Edit product name, description, and cell-project mapping">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="shrink-0"
                      onClick={() => setEditingProductId(product.id)}
                      aria-label="Edit product"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                  </HelpTip>
                </div>
                <div className="mt-3 divide-y">
                  <ResponsiveTableCardRow label="Cells">
                    <div className="flex flex-col items-end gap-1">
                      {product.cells.map((c) => (
                        <span
                          key={`${c.team}-${c.project_id}`}
                          className="text-xs text-muted-foreground"
                        >
                          {TEAM_LABELS[c.team] ?? c.team}: {c.project_name || "—"}
                        </span>
                      ))}
                      {product.cells.length === 0 && (
                        <span className="text-muted-foreground text-xs">
                          Unmapped
                        </span>
                      )}
                    </div>
                  </ResponsiveTableCardRow>
                  <ResponsiveTableCardRow label="Progress">
                    <ProgressCell progress={product.progress} />
                  </ResponsiveTableCardRow>
                </div>
              </ResponsiveTableCard>
            ))}
          </ResponsiveTableCardList>
        }
      />

      {/* Edit Product Dialog */}
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
