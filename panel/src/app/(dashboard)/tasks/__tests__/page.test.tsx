import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageRefreshProvider } from "@/components/providers";
import type { ReactNode } from "react";

type TaskRow = { id: string; project_id: string | null };

const { list, taskTable, searchParams } = vi.hoisted(() => ({
  list: vi.fn<(filters?: Record<string, unknown>) => Promise<TaskRow[]>>(
    async () => [],
  ),
  taskTable: vi.fn<(props: { tasks: TaskRow[] }) => null>(() => null),
  searchParams: { value: new URLSearchParams("status=completed&team=backend") },
}));

vi.mock("@/lib/api/tasks", () => ({ tasksApi: { list } }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams.value,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/hooks/use-projects", () => ({
  useProjects: () => ({ data: [] }),
}));

vi.mock("@/hooks/use-products", () => ({
  useProducts: () => ({ data: [] }),
}));

vi.mock("@/components/tasks", () => ({
  CreateTaskDialog: () => null,
  TaskFilters: () => null,
  TaskTable: taskTable,
}));

import TasksPage from "../page";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function wrapper(ui: ReactNode) {
  return withQueryClient(<PageRefreshProvider>{ui}</PageRefreshProvider>);
}

describe("TasksPage — passes status/team/limit server-side (H17)", () => {
  beforeEach(() => {
    list.mockReset();
    list.mockResolvedValue([]);
    searchParams.value = new URLSearchParams("status=completed&team=backend");
  });

  it("forwards single status + team + limit=500 to tasksApi.list", async () => {
    render(wrapper(<TasksPage />));
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({
        status: "completed",
        team: "backend",
        limit: 500,
      }),
    );
  });
});

describe("TasksPage — ?project=<id> deep-link drill-down", () => {
  beforeEach(() => {
    list.mockReset();
    taskTable.mockClear();
    searchParams.value = new URLSearchParams(
      "project=11111111-1111-1111-1111-111111111111",
    );
  });

  // The portfolio cards deep-link /tasks?project=<project_id>; this is the
  // receiving end of that contract — the filter matches task.project_id, so
  // the linked project's tasks render and nothing else does.
  it("renders only the linked project's tasks when ?project carries a project id", async () => {
    list.mockResolvedValue([
      { id: "t-1", project_id: "11111111-1111-1111-1111-111111111111" },
      { id: "t-2", project_id: "22222222-2222-2222-2222-222222222222" },
      { id: "t-3", project_id: null },
    ]);
    render(wrapper(<TasksPage />));
    // The table renders immediately with an empty list before the query
    // resolves — wait until the fetched rows have flowed through the filter.
    await waitFor(() =>
      expect(taskTable.mock.calls.at(-1)?.[0].tasks.map((t) => t.id)).toEqual([
        "t-1",
      ]),
    );
  });

  it("renders every task when no project param is present", async () => {
    searchParams.value = new URLSearchParams();
    list.mockResolvedValue([
      { id: "t-1", project_id: "11111111-1111-1111-1111-111111111111" },
      { id: "t-3", project_id: null },
    ]);
    render(wrapper(<TasksPage />));
    await waitFor(() =>
      expect(taskTable.mock.calls.at(-1)?.[0].tasks.map((t) => t.id)).toEqual([
        "t-1",
        "t-3",
      ]),
    );
  });
});
