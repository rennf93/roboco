import axios, { AxiosInstance, AxiosError } from "axios";
import { toast } from "sonner";
import { API_URL, CEO_AGENT_ID, CEO_ROLE } from "@/lib/constants";
import { useRateLimitStore } from "@/store/rate-limit-store";
import type { RateLimitHitEvent } from "@/types/rate-limits";

// Custom Axios config extension for retry tracking
declare module "axios" {
  interface InternalAxiosRequestConfig {
    _retryCount?: number;
  }
}

const RATE_LIMIT_MAX_RETRIES = 3;

// GET/PUT are safe to auto-retry on a 429 — GET has no side effect and PUT is
// a full-resource replace, so replaying it is a no-op past the first apply.
// POST/PATCH/DELETE are NOT safe by default (a replayed POST can create a
// duplicate task, a replayed DELETE/PATCH can double-apply a partial update)
// — they only retry when the caller attached an idempotency key the backend
// can dedupe on. Header name matches what a future idempotency-key-bearing
// caller would set; no call site sets it yet, so these methods currently
// skip the auto-retry entirely rather than risking a duplicate side effect.
const RETRY_SAFE_METHODS = new Set(["get", "put"]);
const IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key";

export function isRetrySafe(config: AxiosError["config"]): boolean {
  if (!config) return false;
  const method = (config.method ?? "get").toLowerCase();
  if (RETRY_SAFE_METHODS.has(method)) return true;
  return Boolean(config.headers?.has?.(IDEMPOTENCY_KEY_HEADER));
}

// Create axios instance with default config
// axios ^1.16.0 audit (2026-07-09): every call in panel/src rides this
// browser instance (no proxy option, no maxRedirects/adapter override, no
// manual form-data upload). axios's node-only transitives that changed with
// this bump — follow-redirects, proxy-from-env, form-data — are exercised
// only by axios's Node http adapter, which the panel never invokes; no
// compatibility fix is needed here.
const api: AxiosInstance = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 60000, // Increased to 60s for long operations like reindexing
  // Rides the cloud-auth session cookie. Harmless when cloud auth is off
  // (same-origin requests already carry cookies regardless), and required
  // for a cross-origin dev setup that talks to the backend directly.
  withCredentials: true,
});

// Request interceptor to add auth headers and logging
api.interceptors.request.use(
  (config) => {
    // Add agent context headers for API authorization. A caller that already
    // set X-Agent-ID/X-Agent-Role (e.g. the CEO-initiated A2A DM composer,
    // whose target route resolves the caller's identity from this raw header
    // rather than a DB lookup, and needs the literal "ceo" slug — not the
    // CEO's UUID) wins; every other call keeps defaulting to the CEO
    // identity. has()/set(), not bracket access — AxiosHeaders brackets are
    // case-SENSITIVE, so a caller's lowercase header key would be silently
    // clobbered by the default.
    if (!config.headers.has("X-Agent-ID"))
      config.headers.set("X-Agent-ID", CEO_AGENT_ID);
    if (!config.headers.has("X-Agent-Role"))
      config.headers.set("X-Agent-Role", CEO_ROLE);

    // Log request in development
    if (process.env.NODE_ENV === "development") {
      console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    }

    return config;
  },
  (error) => {
    console.error("[API] Request setup error:", error);
    return Promise.reject(error);
  },
);

/**
 * The Telegram Mini App owns its auth UX: /tg signs in via initData and
 * renders its own wall. Global mounts (the agent-roster sync) can 401 there
 * before that sign-in lands — bouncing the webview to the password /login
 * page it cannot complete would hijack the cockpit.
 */
export function isTgSurfacePath(pathname: string): boolean {
  return /^\/tg(\/|$)/.test(pathname);
}

// A 401 only means "log in" when cloud auth is actually on. In header-trust /
// secure mode (cloud auth off) a 401 is a misconfigured agent token, not a
// missing session — bouncing to /login would dead-end on a page whose backend
// route isn't mounted. Probe the public status endpoint (a bare fetch so it
// doesn't re-enter this interceptor) and only redirect when cloud auth is on.
async function redirectToLoginIfCloudAuth(): Promise<void> {
  if (typeof window === "undefined" || window.location.pathname === "/login") {
    return;
  }
  if (isTgSurfacePath(window.location.pathname)) {
    return;
  }
  try {
    const res = await fetch(`${API_URL}/auth/status`, {
      credentials: "include",
    });
    if (!res.ok) return;
    const data = (await res.json()) as { cloud_auth_enabled?: boolean };
    if (data.cloud_auth_enabled) {
      window.location.href = "/login";
    }
  } catch {
    // Can't confirm cloud auth is on -> don't dead-end the user on /login.
  }
}

// Response interceptor for comprehensive error handling
api.interceptors.response.use(
  (response) => {
    // Log successful responses in development
    if (process.env.NODE_ENV === "development") {
      console.log(
        `[API] ✓ ${response.config.method?.toUpperCase()} ${response.config.url}`,
      );
    }
    return response;
  },
  (error: AxiosError) => {
    // Extract error details
    const status = error.response?.status;
    const url = error.config?.url;
    const method = error.config?.method?.toUpperCase();
    const errorData = error.response?.data as
      | Record<string, unknown>
      | undefined;
    const errorDetail = errorData?.detail || error.message;

    // -------------------------------------------------------------------------
    // 429 Rate-limit handling — FIRST side-effect, before any other logic
    // -------------------------------------------------------------------------
    if (status === 429) {
      const retryAfterHeader = error.response?.headers?.["retry-after"];
      const retryAfterSeconds = retryAfterHeader
        ? parseInt(String(retryAfterHeader), 10)
        : 60;
      const safeRetryAfter = isNaN(retryAfterSeconds) ? 60 : retryAfterSeconds;

      // Extract provider from custom header or fall back to URL path heuristics
      const providerHeader = error.response?.headers?.["x-provider"];
      const urlProvider = url
        ? (["anthropic", "openai", "ollama"].find((p) => url.includes(p)) ??
          "unknown")
        : "unknown";
      const provider = (providerHeader as string | undefined) ?? urlProvider;

      // Dispatch to store as first side-effect
      const hitEvent: RateLimitHitEvent = {
        type: "RATE_LIMIT_HIT",
        provider,
        affectedAgents: [],
        retryAfterSeconds: safeRetryAfter,
        timestamp: new Date().toISOString(),
      };
      useRateLimitStore.getState().hitRateLimit(hitEvent);

      if (isRetrySafe(error.config)) {
        // Track retry count; retry the request (after backoff delay) until exhausted, then toast
        const retryCount = (error.config?._retryCount ?? 0) + 1;
        if (error.config) {
          error.config._retryCount = retryCount;
          if (retryCount < RATE_LIMIT_MAX_RETRIES) {
            // Wait retryAfterSeconds before retrying — interceptor re-runs on each subsequent 429
            const delayMs = safeRetryAfter * 1000;
            return new Promise<void>((resolve) =>
              setTimeout(resolve, delayMs),
            ).then(() => api(error.config!));
          }
        }
        // Retries exhausted — notify the user via Sonner toast
        toast.warning(
          `Rate limited by ${provider}. The system has paused operations and will resume automatically in ~${safeRetryAfter}s.`,
        );
      } else {
        // A non-idempotent write (POST/PATCH/DELETE) never auto-retries —
        // replaying it could double-apply the action. Surface it once instead.
        toast.warning(
          `Rate limited by ${provider}. This action was not automatically retried to avoid duplicating it — please try again in ~${safeRetryAfter}s.`,
        );
      }
    }

    // Log comprehensive error info
    console.error(`[API] ✗ ${method} ${url}`, {
      status,
      detail: errorDetail,
      error: error.message,
    });

    // Specific error handling with helpful messages
    if (error.code === "ECONNABORTED") {
      console.error(
        "[API] Request timed out - backend may be overloaded or unavailable",
      );
    } else if (error.code === "ERR_NETWORK") {
      console.error(
        "[API] Network error - check if backend is running at",
        API_URL,
      );
    } else if (status === 401) {
      console.error("[API] Unauthorized - check API authentication headers");
      void redirectToLoginIfCloudAuth();
    } else if (status === 403) {
      console.error(
        "[API] Forbidden - insufficient permissions for this action",
      );
    } else if (status === 404) {
      console.error("[API] Not found - endpoint may not exist:", url);
    } else if (status === 422) {
      console.error(
        "[API] Validation error - request data is invalid:",
        errorDetail,
      );
    } else if (status && status >= 500) {
      console.error(
        "[API] Server error - backend encountered an internal error",
      );
    }

    return Promise.reject(error);
  },
);

/**
 * Turn a FastAPI `detail` payload — a string, a validation array
 * ([{loc, msg, ...}]), or a structured `{error, message}` object — into one
 * readable line. Returns "" when nothing useful can be extracted.
 */
function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const e = item as { loc?: unknown[]; msg?: unknown };
          const loc = Array.isArray(e.loc)
            ? e.loc.filter((p) => p !== "body").join(".")
            : "";
          const msg = String(e.msg ?? "");
          return loc ? `${loc}: ${msg}` : msg;
        }
        return typeof item === "string" ? item : "";
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === "string") return obj.message;
    if (typeof obj.error === "string") return obj.error;
  }
  return "";
}

/**
 * Helper to extract user-friendly error message from API error
 */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<{ detail?: unknown }>;

    // Check for specific error codes
    if (error.code === "ECONNABORTED") {
      return "Request timed out. The server may be busy.";
    }
    if (error.code === "ERR_NETWORK") {
      return "Cannot connect to server. Check if the backend is running.";
    }

    // Check for API error response. `detail` may be a plain string, a FastAPI
    // validation array ([{loc, msg, ...}]), or a structured envelope object —
    // never blindly String() it (that yields "[object Object]").
    const detail = axiosError.response?.data?.detail;
    if (detail) {
      const formatted = formatErrorDetail(detail);
      if (formatted) return formatted;
    }

    // Check for HTTP status
    const status = axiosError.response?.status;
    if (status === 401)
      return "Authentication required. Please refresh the page.";
    if (status === 403) return "Permission denied for this action.";
    if (status === 404) return "The requested resource was not found.";
    if (status === 422) return "Invalid request data.";
    if (status && status >= 500) return "Server error. Please try again later.";
  }

  // Generic error
  return error instanceof Error
    ? error.message
    : "An unexpected error occurred";
}

export { api, API_URL };
export default api;
