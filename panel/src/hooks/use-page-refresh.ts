"use client";

import { useContext } from "react";
import { PageRefreshContext } from "@/store/page-refresh-context";

/**
 * Public hook for consuming the page-scoped refresh context.
 *
 * Throws if used outside of `PageRefreshProvider`. Returns the full context
 * value so callers can register page-level refresh callbacks or trigger the
 * active scope's refresh from global UI such as the navbar.
 */
export function usePageRefresh() {
  const context = useContext(PageRefreshContext);
  if (!context) {
    throw new Error("usePageRefresh must be used within a PageRefreshProvider");
  }
  return context;
}
