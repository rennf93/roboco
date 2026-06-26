/**
 * Tests for getErrorMessage() from @/lib/api/client.
 *
 * Strategy: We construct minimal objects that satisfy the AxiosError contract.
 * The real `axios.isAxiosError` implementation checks:
 *   isObject(payload) && payload.isAxiosError === true
 * so plain objects with { isAxiosError: true } are recognised correctly,
 * and we need not fully mock the axios module.
 *
 * We still mock the modules that client.ts imports for side effects so the
 * axios instance creation and interceptor registration don't produce
 * unwanted network calls or store mutations during the test run.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock heavy side-effect imports consumed by client.ts at module level
// ---------------------------------------------------------------------------
vi.mock("sonner", () => ({
  toast: { warning: vi.fn(), error: vi.fn() },
}));

vi.mock("@/store/rate-limit-store", () => ({
  useRateLimitStore: {
    getState: vi.fn(() => ({ hitRateLimit: vi.fn() })),
  },
}));

// ---------------------------------------------------------------------------
// Import the function under test AFTER mocks are in place
// ---------------------------------------------------------------------------
import { getErrorMessage } from "@/lib/api/client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal AxiosError-like object accepted by `axios.isAxiosError`.
 *
 * The real axios checks: isObject(payload) && payload.isAxiosError === true
 */
function makeAxiosError(opts: {
  code?: string;
  status?: number;
  detail?: unknown;
  message?: string;
}) {
  return {
    isAxiosError: true as const,
    code: opts.code,
    message: opts.message ?? "axios error",
    response:
      opts.status !== undefined
        ? {
            status: opts.status,
            data: { detail: opts.detail } as { detail?: unknown },
            headers: {} as Record<string, string>,
            config: {} as never,
            statusText: "",
          }
        : undefined,
    config: {} as never,
    name: "AxiosError" as const,
    toJSON: () => ({}),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Error-code tests
// ---------------------------------------------------------------------------

describe("getErrorMessage — error codes", () => {
  it("returns timeout message for ECONNABORTED", () => {
    const err = makeAxiosError({ code: "ECONNABORTED" });
    expect(getErrorMessage(err)).toBe(
      "Request timed out. The server may be busy.",
    );
  });

  it("returns network message for ERR_NETWORK", () => {
    const err = makeAxiosError({ code: "ERR_NETWORK" });
    expect(getErrorMessage(err)).toBe(
      "Cannot connect to server. Check if the backend is running.",
    );
  });
});

// ---------------------------------------------------------------------------
// `detail` payload format tests (formatErrorDetail branches)
// ---------------------------------------------------------------------------

describe("getErrorMessage — detail string format", () => {
  it("returns the string detail directly", () => {
    const err = makeAxiosError({ status: 400, detail: "Some error string" });
    expect(getErrorMessage(err)).toBe("Some error string");
  });
});

describe("getErrorMessage — detail array format", () => {
  it("formats a FastAPI validation array into a readable string", () => {
    const err = makeAxiosError({
      status: 422,
      detail: [
        { loc: ["body", "name"], msg: "field required" },
        { loc: ["body", "email"], msg: "invalid email" },
      ],
    });
    const result = getErrorMessage(err);
    // Both validation errors should appear
    expect(result).toContain("name");
    expect(result).toContain("field required");
    expect(result).toContain("email");
    expect(result).toContain("invalid email");
  });

  it("returns a plain string item inside the array as-is", () => {
    const err = makeAxiosError({
      status: 400,
      detail: ["plain error"],
    });
    expect(getErrorMessage(err)).toBe("plain error");
  });
});

describe("getErrorMessage — detail object format", () => {
  it("extracts `message` field from a structured error object", () => {
    const err = makeAxiosError({
      status: 400,
      detail: { message: "Structured message" },
    });
    expect(getErrorMessage(err)).toBe("Structured message");
  });

  it("extracts `error` field when `message` is absent", () => {
    const err = makeAxiosError({
      status: 400,
      detail: { error: "Error field content" },
    });
    expect(getErrorMessage(err)).toBe("Error field content");
  });
});

// ---------------------------------------------------------------------------
// HTTP status code tests (no usable detail)
// ---------------------------------------------------------------------------

describe("getErrorMessage — HTTP status codes", () => {
  it("returns auth message for 401", () => {
    const err = makeAxiosError({ status: 401 });
    expect(getErrorMessage(err)).toBe(
      "Authentication required. Please refresh the page.",
    );
  });

  it("returns permission message for 403", () => {
    const err = makeAxiosError({ status: 403 });
    expect(getErrorMessage(err)).toBe("Permission denied for this action.");
  });

  it("returns not-found message for 404", () => {
    const err = makeAxiosError({ status: 404 });
    expect(getErrorMessage(err)).toBe("The requested resource was not found.");
  });

  it("returns validation message for 422 without detail", () => {
    const err = makeAxiosError({ status: 422 });
    expect(getErrorMessage(err)).toBe("Invalid request data.");
  });

  it("returns server-error message for 500", () => {
    const err = makeAxiosError({ status: 500 });
    expect(getErrorMessage(err)).toBe("Server error. Please try again later.");
  });

  it("returns server-error message for 502 (>= 500)", () => {
    const err = makeAxiosError({ status: 502 });
    expect(getErrorMessage(err)).toBe("Server error. Please try again later.");
  });
});

// ---------------------------------------------------------------------------
// Fallback tests (non-Axios errors)
// ---------------------------------------------------------------------------

describe("getErrorMessage — non-Axios fallbacks", () => {
  it("returns Error.message for a plain Error instance", () => {
    const err = new Error("plain JS error");
    expect(getErrorMessage(err)).toBe("plain JS error");
  });

  it("returns generic message for a string input", () => {
    expect(getErrorMessage("some string")).toBe("An unexpected error occurred");
  });

  it("returns generic message for null input", () => {
    expect(getErrorMessage(null)).toBe("An unexpected error occurred");
  });

  it("returns generic message for undefined input", () => {
    expect(getErrorMessage(undefined)).toBe("An unexpected error occurred");
  });

  it("returns generic message for a plain object without isAxiosError", () => {
    expect(getErrorMessage({ code: "SOME_CODE" })).toBe(
      "An unexpected error occurred",
    );
  });
});
