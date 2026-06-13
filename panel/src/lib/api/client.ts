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

// Create axios instance with default config
const api: AxiosInstance = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 60000, // Increased to 60s for long operations like reindexing
});

// Request interceptor to add auth headers and logging
api.interceptors.request.use(
  (config) => {
    // Add agent context headers for API authorization
    config.headers["X-Agent-ID"] = CEO_AGENT_ID;
    config.headers["X-Agent-Role"] = CEO_ROLE;

    // Log request in development
    if (process.env.NODE_ENV === "development") {
      console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    }

    return config;
  },
  (error) => {
    console.error("[API] Request setup error:", error);
    return Promise.reject(error);
  }
);

// Response interceptor for comprehensive error handling
api.interceptors.response.use(
  (response) => {
    // Log successful responses in development
    if (process.env.NODE_ENV === "development") {
      console.log(`[API] ✓ ${response.config.method?.toUpperCase()} ${response.config.url}`);
    }
    return response;
  },
  (error: AxiosError) => {
    // Extract error details
    const status = error.response?.status;
    const url = error.config?.url;
    const method = error.config?.method?.toUpperCase();
    const errorData = error.response?.data as Record<string, unknown> | undefined;
    const errorDetail = errorData?.detail || error.message;

    // -------------------------------------------------------------------------
    // 429 Rate-limit handling — FIRST side-effect, before any other logic
    // -------------------------------------------------------------------------
    if (status === 429) {
      const retryAfterHeader = error.response?.headers?.["retry-after"];
      const retryAfterSeconds = retryAfterHeader ? parseInt(String(retryAfterHeader), 10) : 60;
      const safeRetryAfter = isNaN(retryAfterSeconds) ? 60 : retryAfterSeconds;

      // Extract provider from custom header or fall back to URL path heuristics
      const providerHeader = error.response?.headers?.["x-provider"];
      const urlProvider = url
        ? (["anthropic", "openai", "ollama"].find((p) => url.includes(p)) ?? "unknown")
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

      // Track retry count; retry the request (after backoff delay) until exhausted, then toast
      const retryCount = (error.config?._retryCount ?? 0) + 1;
      if (error.config) {
        error.config._retryCount = retryCount;
        if (retryCount < RATE_LIMIT_MAX_RETRIES) {
          // Wait retryAfterSeconds before retrying — interceptor re-runs on each subsequent 429
          const delayMs = safeRetryAfter * 1000;
          return new Promise<void>((resolve) => setTimeout(resolve, delayMs)).then(
            () => api(error.config!)
          );
        }
      }
      // Retries exhausted — notify the user via Sonner toast
      toast.warning(
        `Rate limited by ${provider}. The system has paused operations and will resume automatically in ~${safeRetryAfter}s.`
      );
    }

    // Log comprehensive error info
    console.error(`[API] ✗ ${method} ${url}`, {
      status,
      detail: errorDetail,
      error: error.message,
    });

    // Specific error handling with helpful messages
    if (error.code === "ECONNABORTED") {
      console.error("[API] Request timed out - backend may be overloaded or unavailable");
    } else if (error.code === "ERR_NETWORK") {
      console.error("[API] Network error - check if backend is running at", API_URL);
    } else if (status === 401) {
      console.error("[API] Unauthorized - check API authentication headers");
    } else if (status === 403) {
      console.error("[API] Forbidden - insufficient permissions for this action");
    } else if (status === 404) {
      console.error("[API] Not found - endpoint may not exist:", url);
    } else if (status === 422) {
      console.error("[API] Validation error - request data is invalid:", errorDetail);
    } else if (status && status >= 500) {
      console.error("[API] Server error - backend encountered an internal error");
    }

    return Promise.reject(error);
  }
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
    if (status === 401) return "Authentication required. Please refresh the page.";
    if (status === 403) return "Permission denied for this action.";
    if (status === 404) return "The requested resource was not found.";
    if (status === 422) return "Invalid request data.";
    if (status && status >= 500) return "Server error. Please try again later.";
  }

  // Generic error
  return error instanceof Error ? error.message : "An unexpected error occurred";
}

export { api, API_URL };
export default api;
