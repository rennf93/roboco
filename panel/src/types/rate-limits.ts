// =============================================================================
// RATE LIMIT TYPES
// =============================================================================

/**
 * Represents an active rate-limit entry for a provider.
 * Stored in the Zustand store keyed by provider name.
 */
export interface RateLimitEntry {
  /** The AI provider that is rate-limited (e.g. "anthropic", "openai") */
  provider: string;
  /** Agent slugs affected by this rate limit */
  affectedAgents: string[];
  /** ISO timestamp when the rate limit was hit */
  hitAt: string;
  /** ISO timestamp when the rate limit is expected to lift (hitAt + retryAfterSeconds) */
  resumeAt: string;
  /** How many seconds until operations resume */
  retryAfterSeconds: number;
}

/**
 * WebSocket event emitted when a rate limit is triggered.
 */
export interface RateLimitHitEvent {
  type: "RATE_LIMIT_HIT";
  /** The AI provider being rate-limited */
  provider: string;
  /** Agent slugs affected */
  affectedAgents: string[];
  /** How many seconds to wait before retrying */
  retryAfterSeconds: number;
  /** ISO timestamp of the event */
  timestamp: string;
}

/**
 * WebSocket event emitted when a rate limit is cleared.
 */
export interface RateLimitLiftedEvent {
  type: "RATE_LIMIT_LIFTED";
  /** The AI provider whose rate limit has been lifted */
  provider: string;
  /** ISO timestamp of the event */
  timestamp: string;
}

/**
 * Response shape from GET /api/system/rate-limits
 */
export interface RateLimitApiResponse {
  entries: RateLimitEntry[];
}
