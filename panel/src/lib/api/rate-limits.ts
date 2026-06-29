import api from "./client";
import type { RateLimitApiResponse } from "@/types/rate-limits";
import { isMockMode } from "@/lib/mock-data";

export const rateLimitsApi = {
  /**
   * GET /api/system/rate-limits — fetch active rate limits on page load or WS reconnect.
   * Returns empty list in mock mode (rate limits are a real-backend-only concern).
   */
  getRateLimits: async (): Promise<RateLimitApiResponse> => {
    if (isMockMode()) {
      console.warn(
        "[rate-limits] isMockMode: skipping GET /api/system/rate-limits",
      );
      return { entries: [] };
    }
    const { data } = await api.get<RateLimitApiResponse>("/system/rate-limits");
    return data;
  },
};
