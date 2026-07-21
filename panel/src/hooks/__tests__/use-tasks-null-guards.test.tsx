import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";

// Regression coverage for the data-hook null-guard audit: undefined/empty ids
// must never reach the API (the `enabled` guard), and useTask's board-review
// poll must stop the moment board_review_complete flips — the two things
// called out as "known suspects" in the audit task. TanStack Query already
// tears the poll's internal timer down on unmount (its Observer
// unsubscribes), so the real regression risk is the poll condition itself,
// not a missing cleanup — this exercises the condition end-to-end with fake
// timers rather than re-deriving it in the test.

const { get, getSubtasks } = vi.hoisted(() => ({
  get: vi.fn(),
  getSubtasks: vi.fn(),
}));

vi.mock("@/lib/api/tasks", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/tasks")>(
      "@/lib/api/tasks",
    );
  return {
    ...actual,
    tasksApi: { ...actual.tasksApi, get, getSubtasks },
  };
});

import { useTask, useSubtasks } from "@/hooks/use-tasks";
import { Team } from "@/types";
import type { Task } from "@/types";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("data-hook null-guard audit", () => {
  beforeEach(() => {
    get.mockReset();
    getSubtasks.mockReset();
  });

  it("useTask never calls the API when taskId is empty", () => {
    renderHook(() => useTask(""), { wrapper });
    expect(get).not.toHaveBeenCalled();
  });

  it("useSubtasks never calls the API when parentTaskId is empty", () => {
    renderHook(() => useSubtasks(""), { wrapper });
    expect(getSubtasks).not.toHaveBeenCalled();
  });

  it("useTask fetches once a real id is supplied", async () => {
    get.mockResolvedValue({ id: "t1", team: Team.BACKEND } as Task);
    renderHook(() => useTask("t1"), { wrapper });
    await waitFor(() => expect(get).toHaveBeenCalledWith("t1"));
  });

  describe("board-review poll stops itself", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("polls a board task every 4s until board_review_complete flips true", async () => {
      get.mockResolvedValue({
        id: "board-task",
        team: Team.BOARD,
        board_review_complete: false,
      } as Task);

      renderHook(() => useTask("board-task"), { wrapper });
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1));

      // Still incomplete — the poll must fire again after 4s.
      await vi.advanceTimersByTimeAsync(4000);
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2));

      // Board finishes reviewing — the next poll response reports completion.
      get.mockResolvedValue({
        id: "board-task",
        team: Team.BOARD,
        board_review_complete: true,
      } as Task);
      await vi.advanceTimersByTimeAsync(4000);
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(3));

      // No further poll should be scheduled once complete.
      await vi.advanceTimersByTimeAsync(10000);
      expect(get).toHaveBeenCalledTimes(3);
    });

    it("never polls a non-board task", async () => {
      get.mockResolvedValue({ id: "t1", team: Team.BACKEND } as Task);
      renderHook(() => useTask("t1"), { wrapper });
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1));

      await vi.advanceTimersByTimeAsync(10000);
      expect(get).toHaveBeenCalledTimes(1);
    });
  });
});
