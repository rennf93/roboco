import axios from "axios";

import api from "./client";

// =============================================================================
// System health: review-path tool outage (frontend half of the frozen
// cross-cell contract; the backend cell owns the detection/health event and
// the exact response schema).
//
//   GET /api/system-health/review-path
//     -> { active, outage_type, window_start, recovered }
//
// Frozen contract fields: outage active flag, outage type
// ("review_path_tools"), window start timestamp, recovered state. Field
// names beyond these are the cells' shared choice; deviations are
// re-synced via main-pm and land here first.
//
// Read-only on the panel: this surfaces the outage, it never acts on it.
// =============================================================================

export type OutageType = "review_path_tools";

export interface ReviewPathOutageStatus {
  /** True while the cross-task tool outage is ongoing. */
  active: boolean;
  /** Which tool path is degraded; currently always "review_path_tools". */
  outage_type: OutageType;
  /** ISO 8601 start of the outage window; null when no outage. */
  window_start: string | null;
  /** True once reads recovered and the backend cleared the event. */
  recovered: boolean;
}

export const systemHealthApi = {
  // Returns null when the surface is not there yet (backend slice not
  // merged, or a proxy strips the route) so callers degrade to "no banner"
  // instead of erroring — the outage banner must never become its own
  // outage.
  getReviewPathOutage: async (): Promise<ReviewPathOutageStatus | null> => {
    try {
      const { data } =
        await api.get<ReviewPathOutageStatus>("/system-health/review-path");
      return data;
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        return null;
      }
      throw error;
    }
  },
};
