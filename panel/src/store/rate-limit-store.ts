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
    set(() => {
      const next = new Map<string, RateLimitEntry>();
      for (const entry of response.entries) {
        next.set(entry.provider, entry);
      }
      return { limits: next };
    }),
}));
