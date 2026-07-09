"use client";

import * as React from "react";
import {
  PageRefreshContext,
  PageRefreshState,
  RefreshCallback,
} from "@/components/providers/page-refresh-provider";

export type { PageRefreshState, RefreshCallback };

/**
 * Consume the nearest {@link PageRefreshProvider}.
 *
 * Returns a stable API for registering/unregistering refresh callbacks and for
 * reading the shared disabled/loading state. Throws when called outside a
 * provider so consumers fail fast instead of silently missing refreshes.
 *
 * @example
 * ```tsx
 * const { register, unregister, refresh, loading, disabled } = usePageRefresh();
 *
 * useEffect(() => {
 *   const cb = () => refetch();
 *   register(cb);
 *   return () => unregister(cb);
 * }, [register, unregister, refetch]);
 * ```
 */
export function usePageRefresh(): PageRefreshState {
  const context = React.useContext(PageRefreshContext);

  if (!context) {
    throw new Error("usePageRefresh must be used within a PageRefreshProvider");
  }

  return context;
}
