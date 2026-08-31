import { describe, it, expect, vi } from "vitest";
import type { SpawnWasteReport } from "@/types";

// Regression guard against the spawn-waste endpoint drifting back to
// /usage/spawn-waste — that wrong-endpoint failure mode shipped once before
// (zero-output-token metric) because consumer tests mock the api module
// wholesale. This is the ONE test on the observability client that actually
// exercises the fetcher against a mocked HTTP client.
const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("../client", () => ({
  default: { get },
}));

import { observabilityApi } from "../observability";

describe("observabilityApi.getSpawnWaste", () => {
  it("requests GET /dashboard/metrics/spawn-waste and returns the response verbatim", async () => {
    const payload: SpawnWasteReport = {
      total_sessions: 14,
      zero_progress_sessions: 3,
      zero_progress_cost_usd: 1.2345,
      total_cost_usd: 98.76,
      zero_progress_cost_share: 0.0125,
      by_agent: [
        {
          agent_slug: "fe-dev-2",
          sessions: 4,
          zero_progress_sessions: 1,
          zero_progress_cost_usd: 0.41,
          rate: 0.25,
        },
      ],
      by_team: [
        {
          team: "frontend",
          sessions: 9,
          zero_progress_sessions: 2,
          zero_progress_cost_usd: 0.87,
          rate: 0.2222,
        },
      ],
      by_task: [
        {
          task_id: "task-3",
          sessions: 2,
          zero_progress_sessions: 1,
          zero_progress_cost_usd: 0.41,
          rate: 0.5,
        },
      ],
    };
    get.mockResolvedValue({ data: payload });

    const result = await observabilityApi.getSpawnWaste(30);

    expect(get).toHaveBeenCalledWith("/dashboard/metrics/spawn-waste", {
      params: { days: 30 },
    });
    expect(result).toEqual(payload);
  });
});