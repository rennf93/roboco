"use client";

import { Suspense, useEffect } from "react";
import { useProducts } from "@/hooks/use-products";
import { OfflineState } from "@/components/ui/offline-state";
import { CreateProductDialog, ProductTable } from "@/components/products";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageRefresh } from "@/hooks";

function ProductsPageContent() {
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

// Wrap in Suspense to match the dashboard page convention
export default function ProductsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-32 mb-2" />
              <Skeleton className="h-5 w-64" />
            </div>
          </div>
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <ProductsPageContent />
    </Suspense>
  );
}
