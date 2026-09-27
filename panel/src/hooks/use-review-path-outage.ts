"use client";

import { useQuery } from "@tanstack/react-query";
import {
  systemHealthApi,
  type ReviewPathOutageStatus,
} from "@/lib/api/system-health";

export const systemHealthKeys = {
  all: ["system-health"] as const,
  reviewPathOutage: () => [...systemHealthKeys.all, "review-path-outage"] as const,
};

/**
 * Polls GET /api/system-health/review-path every 30s — the same cadence the
 * maintenance-pause and rate-limit banners already use. An active review-path
 * outage is unmissable, so the banner rides a background poll rather than a
 * bootstrap payload: it self-clears on recovery without a reload. Polling is
 * also the delivery choice that survives the backend slice landing later —
 * until then the query resolves to null (no surface yet) and nothing renders.
 */
export function useReviewPathOutage(): ReviewPathOutageStatus | null | undefined {
  const query = useQuery({
    queryKey: systemHealthKeys.reviewPathOutage(),
    queryFn: () => systemHealthApi.getReviewPathOutage(),
    refetchInterval: 30000,
  });
  return query.data;
}
