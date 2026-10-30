import { describe, it, expect, vi } from "vitest";
import type { ReleaseCertificate } from "@/lib/api/release";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("../client", () => ({
  default: { get },
}));

import { releaseApi } from "../release";

function buildCertificate(): ReleaseCertificate {
  return {
    version: "0.14.0",
    generated_at: "2026-09-01T00:00:00Z",
    ci_verdict: "green",
    conventions_clean: true,
    ceo_approved_at: "2026-09-01T00:05:00Z",
    changelog_excerpt: "## 0.14.0",
    task_states: [
      {
        task_id: "t1",
        title: "Add metrics",
        status: "completed",
        criteria_total: 2,
        criteria_verified: 2,
        qa_passed: true,
      },
      {
        task_id: "t2",
        title: "Docs sweep",
        status: "completed",
        criteria_total: 0,
        criteria_verified: 0,
        qa_passed: null,
      },
    ],
    findings_summary: {
      open: { blocker: 0, major: 0, minor: 0, nit: 0 },
      closed: { blocker: 1, major: 2, minor: 0, nit: 0 },
      waived: { blocker: 0, major: 0, minor: 1, nit: 0 },
    },
  };
}

describe("releaseApi.getCertificate", () => {
  it("requests GET /releases/{version}/certificate and returns the response verbatim", async () => {
    const payload = buildCertificate();
    get.mockResolvedValue({ data: payload });

    const result = await releaseApi.getCertificate("0.14.0");

    expect(get).toHaveBeenCalledWith("/releases/0.14.0/certificate");
    expect(result).toEqual(payload);
  });

  it("returns null on a 404 (version hasn't published yet) instead of throwing", async () => {
    get.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404 },
    });

    const result = await releaseApi.getCertificate("9.9.9");

    expect(result).toBeNull();
  });

  it("rethrows a non-404 error", async () => {
    get.mockRejectedValue({
      isAxiosError: true,
      response: { status: 500 },
    });

    await expect(releaseApi.getCertificate("0.14.0")).rejects.toBeTruthy();
  });
});
