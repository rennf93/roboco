import { describe, it, expect, vi, afterEach } from "vitest";
import { systemHealthApi } from "../system-health";

// The real api client would hit the network; the backend slice may not be
// merged, so axios is mocked and the 404 → null degradation is pinned here.
vi.mock("../client", () => ({
  default: Object.assign(vi.fn(), {
    isAxiosError: (e: unknown) =>
      !!e && typeof e === "object" && "isAxiosError" in e,
    get: vi.fn(),
  }),
}));

import api from "../client";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

afterEach(() => {
  mockedGet.mockReset();
});

describe("systemHealthApi.getReviewPathOutage", () => {
  it("returns the outage status from the health surface", async () => {
    const status = {
      active: true,
      outage_type: "review_path_tools",
      window_start: "2026-09-05T09:00:00Z",
      recovered: false,
    };
    mockedGet.mockResolvedValue({ data: status });
    await expect(systemHealthApi.getReviewPathOutage()).resolves.toEqual(status);
    expect(mockedGet).toHaveBeenCalledWith("/system-health/review-path");
  });

  it("degrades to null on a 404 (backend surface not merged yet)", async () => {
    mockedGet.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404 },
    });
    await expect(systemHealthApi.getReviewPathOutage()).resolves.toBeNull();
  });

  it("rethrows non-404 errors", async () => {
    mockedGet.mockRejectedValue({ isAxiosError: true, response: { status: 500 } });
    await expect(systemHealthApi.getReviewPathOutage()).rejects.toEqual({
      isAxiosError: true,
      response: { status: 500 },
    });
  });
});
