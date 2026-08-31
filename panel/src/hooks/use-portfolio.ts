"use client";

import { useQuery } from "@tanstack/react-query";
import { dashboardApi } from "@/lib/api/dashboard";
import type { PortfolioCard } from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const portfolioKeys = {
  all: ["portfolio"] as const,
  list: () => [...portfolioKeys.all, "list"] as const,
};

// =============================================================================
// HOOKS
// =============================================================================

/** CEO-gated per-project portfolio metrics (GET /dashboard/portfolio), sorted by active task count descending. */
export function usePortfolio() {
  return useQuery<PortfolioCard[]>({
    queryKey: portfolioKeys.list(),
    queryFn: () => dashboardApi.getPortfolio(),
    refetchInterval: 60_000,
  });
}