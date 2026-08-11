import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageRefreshProvider } from "@/components/providers";
import type { ReactNode } from "react";
import type { StalledTask } from "@/types";

const { list, getStalledTasks, capturedTaskTableProps } = vi.hoisted(() => ({
  list: vi.fn(async () => [
    { id: "task-1", title: "A", status: "in_progress", team: "backend" },
    { id: "task-2", title: "B", status: "in_progress", team: "backend" },
  ]),
  getStalledTasks: vi.fn(async (): Promise<StalledTask[]> => []),
  capturedTaskTableProps: { current: null as { tasks?: unknown[] } | null },
}));

vi.mock("@/lib/api/tasks", () => ({ tasksApi: { list } }));
vi.mock("@/lib/api/dashboard", () => ({
  dashboardApi: { getStalledTasks },
}));

let urlParams = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(urlParams),
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
  TaskTable: (props: { tasks?: unknown[] }) => {
    capturedTaskTableProps.current = props;
    return <div data-testid="task-table-marker" />;
  },
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

describe("TasksPage — stalled filter (URL-param wired, populated)", () => {
  beforeEach(() => {
    urlParams = "stalled=1";
    list.mockClear();
    getStalledTasks.mockReset();
    getStalledTasks.mockResolvedValue([
      {
        task_id: "task-1",
        title: "A",
        assignee_id: null,
        assignee_slug: null,
        status: "in_progress",
        reason: "respawn breaker gave up",
        stalled_since: "2026-08-01T00:00:00Z",
        stalled_seconds: 3600,
      } satisfies StalledTask,
    ]);
    capturedTaskTableProps.current = null;
  });

  it("narrows the list to only tasks in the backend stalled set when ?stalled=1", async () => {
    render(wrapper(<TasksPage />));

    await waitFor(() =>
      expect(capturedTaskTableProps.current?.tasks).toEqual([
        expect.objectContaining({ id: "task-1" }),
      ]),
    );
  });
});

describe("TasksPage — stalled filter (empty backend set)", () => {
  beforeEach(() => {
    urlParams = "stalled=1";
    list.mockClear();
    getStalledTasks.mockReset();
    getStalledTasks.mockResolvedValue([]);
    capturedTaskTableProps.current = null;
  });

  it("renders zero tasks, not the whole unfiltered list", async () => {
    render(wrapper(<TasksPage />));

    await waitFor(() =>
      expect(capturedTaskTableProps.current?.tasks).toEqual([]),
    );
  });
});

describe("TasksPage — stalled filter (backend fetch fails)", () => {
  beforeEach(() => {
    urlParams = "stalled=1";
    list.mockClear();
    getStalledTasks.mockReset();
    getStalledTasks.mockRejectedValue(new Error("network error"));
    capturedTaskTableProps.current = null;
  });

  it("does not silently render an empty table — the table is withheld and an error message renders instead", async () => {
    const { findByText, queryByTestId } = render(wrapper(<TasksPage />));

    await findByText(/failed to load stalled tasks/i);
    // TaskTable is unmounted once the stalled fetch errors — the error
    // message replaces it instead of an indistinguishable empty table.
    expect(queryByTestId("task-table-marker")).not.toBeInTheDocument();
  });
});
