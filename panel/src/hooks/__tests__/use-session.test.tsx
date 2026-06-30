import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createElement } from "react";

// Bundle C: GET /sessions/{id} now returns task_links (with titles) in one shot.
// useSession must rely on that single read and stop the N+1 re-fetch
// (getTasksForSession → tasksApi.get per link) it previously did.
const sessionGet = vi.fn();
const getTasksForSession = vi.fn();
const taskGet = vi.fn();

vi.mock("@/lib/api/sessions", () => ({
  sessionsApi: {
    get: (...args: unknown[]) => sessionGet(...args),
    getTasksForSession: (...args: unknown[]) => getTasksForSession(...args),
  },
}));
vi.mock("@/lib/api/tasks", () => ({
  tasksApi: { get: (...args: unknown[]) => taskGet(...args) },
}));
vi.mock("@/lib/api/channels", () => ({ channelsApi: {} }));
vi.mock("@/lib/api/messages", () => ({ messagesApi: {} }));

import { useSession } from "../use-channels";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

describe("useSession", () => {
  beforeEach(() => {
    sessionGet.mockReset();
    getTasksForSession.mockReset();
    taskGet.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("returns task_links from the single get() call without per-task fetches", async () => {
    sessionGet.mockResolvedValue({
      id: "s1",
      group_id: "g1",
      status: "active",
      scope: "cell",
      message_count: 0,
      total_content_length: 0,
      started_at: "2026-06-30T00:00:00Z",
      last_activity_at: "2026-06-30T00:00:00Z",
      closed_at: null,
      task_links: [
        {
          task_id: "t1",
          task_title: "Build it",
          is_primary: true,
          relationship_type: "discussion",
        },
      ],
    });

    const { result } = renderHook(() => useSession("s1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.task_links).toHaveLength(1);
    expect(result.current.data?.task_links?.[0].task_title).toBe("Build it");
    // The redundant N+1 path must be gone.
    expect(getTasksForSession).not.toHaveBeenCalled();
    expect(taskGet).not.toHaveBeenCalled();
    expect(sessionGet).toHaveBeenCalledTimes(1);
  });
});
