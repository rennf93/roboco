import axios, { AxiosInstance, AxiosError } from "axios";
import { API_URL, CEO_AGENT_ID, CEO_ROLE } from "@/lib/constants";

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
 * Helper to extract user-friendly error message from API error
 */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<{ detail?: string }>;

    // Check for specific error codes
    if (error.code === "ECONNABORTED") {
      return "Request timed out. The server may be busy.";
    }
    if (error.code === "ERR_NETWORK") {
      return "Cannot connect to server. Check if the backend is running.";
    }

    // Check for API error response
    if (axiosError.response?.data?.detail) {
      return String(axiosError.response.data.detail);
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
