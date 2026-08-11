import { describe, it, expect, vi } from "vitest";
import type { StalledTask } from "@/types";

// Regression guard for the getStalledTasks/GET-/tasks/blocked mismatch: this
// is the ONE test in the stalled-tasks suite that actually exercises the
// fetcher against a mocked HTTP client, rather than mocking
// "@/lib/api/dashboard" wholesale (which every consumer test does, and which
// is why the wrong-endpoint bug shipped unnoticed).
const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("../client", () => ({
  default: { get },
}));

import { dashboardApi } from "../dashboard";

describe("dashboardApi.getStalledTasks", () => {
  it("requests GET /dashboard/stalled-tasks and returns the response verbatim", async () => {
    const payload: StalledTask[] = [
      {
        task_id: "task-1",
        title: "Fix flaky test",
        assignee_id: "agent-1",
        assignee_slug: "fe-dev-1",
        status: "in_progress",
        reason: "respawn breaker gave up after 4 strikes",
        stalled_since: "2026-08-01T00:00:00Z",
        stalled_seconds: 120,
      },
    ];
    get.mockResolvedValue({ data: payload });

    const result = await dashboardApi.getStalledTasks();

    expect(get).toHaveBeenCalledWith("/dashboard/stalled-tasks");
    expect(result).toEqual(payload);
  });
});
