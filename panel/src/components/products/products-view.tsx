"use client";

import { useEffect } from "react";
import { useProducts } from "@/hooks/use-products";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProductDialog } from "@/components/products/create-product-dialog";
import { ProductTable } from "@/components/products/product-table";
import { usePageRefresh } from "@/hooks";

/** Products tab content — extracted from the standalone /products page so it
 * can live inside the Workstation tab shell (see workstation/page.tsx). */
export function ProductsView() {
  const { data: products, isLoading, error, refetch } = useProducts();

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  // Check if it's a connection error (backend not running)
  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      error.message?.includes("ECONNREFUSED") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Products</h1>
          <p className="text-muted-foreground">
            Map each cell to the project it works on for a product
          </p>
        </div>
        <div className="flex items-center gap-2">
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
      ) : (
        <ProductTable products={products} isLoading={isLoading} />
      )}
    </div>
  );
}
