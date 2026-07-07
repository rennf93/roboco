import { create } from "zustand";
import type {
  RateLimitEntry,
  RateLimitHitEvent,
  RateLimitLiftedEvent,
  RateLimitApiResponse,
} from "@/types/rate-limits";

interface RateLimitState {
  /** Active rate limits keyed by provider name */
  limits: Map<string, RateLimitEntry>;

  // Actions
  hitRateLimit: (event: RateLimitHitEvent) => void;
  liftRateLimit: (event: RateLimitLiftedEvent) => void;
  syncFromApi: (response: RateLimitApiResponse) => void;
}

export const useRateLimitStore = create<RateLimitState>((set) => ({
  limits: new Map<string, RateLimitEntry>(),

  hitRateLimit: (event: RateLimitHitEvent) =>
    set((state) => {
      const next = new Map(state.limits);
      const entry: RateLimitEntry = {
        provider: event.provider,
        affectedAgents: event.affectedAgents,
        hitAt: event.timestamp,
        resumeAt: new Date(
          new Date(event.timestamp).getTime() + event.retryAfterSeconds * 1000,
        ).toISOString(),
        retryAfterSeconds: event.retryAfterSeconds,
      };
      next.set(event.provider, entry);
      return { limits: next };
    }),

  liftRateLimit: (event: RateLimitLiftedEvent) =>
    set((state) => {
      const next = new Map(state.limits);
      next.delete(event.provider);
      return { limits: next };
    }),

  syncFromApi: (response: RateLimitApiResponse) =>
    set((state) => {
      // Merge by freshest hitAt: an out-of-order (older) API snapshot must
      // not regress a fresher WS-derived entry. Entries omitted from the
      // snapshot are retained (a stale/empty poll doesn't wipe live state).
      const next = new Map(state.limits);
      for (const entry of response.entries) {
        const existing = next.get(entry.provider);
        if (
          existing &&
          existing.hitAt &&
          entry.hitAt &&
          existing.hitAt >= entry.hitAt
        ) {
          continue;
        }
        next.set(entry.provider, entry);
      }
      return { limits: next };
    }),
}));
