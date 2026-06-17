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
import { Skeleton } from "@/components/ui/skeleton";
import { Boxes, Pencil } from "lucide-react";
import type { ProductSummary } from "@/types";
import { EditProductDialog } from "./edit-product-dialog";

interface ProductTableProps {
  products: ProductSummary[] | undefined;
  isLoading: boolean;
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
      <div className="border rounded-lg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Cells Mapped</TableHead>
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
                    <p className="text-xs text-muted-foreground font-mono">{product.slug}</p>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge className="bg-blue-500/10 text-blue-500">
                    {product.cell_count} / 3
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setEditingProductId(product.id)}
                      title="Edit product"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

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
