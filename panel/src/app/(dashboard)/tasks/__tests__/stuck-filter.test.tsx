import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageRefreshProvider } from "@/components/providers";
import type { ReactNode } from "react";

// Mocked backend contract: with `stuck: true` the list endpoint returns
// exactly the stuck set. The page must render that set verbatim.
const { list, capturedTaskTableProps } = vi.hoisted(() => ({
  list: vi.fn(),
  capturedTaskTableProps: { current: null as { tasks?: unknown[] } | null },
}));

vi.mock("@/lib/api/tasks", () => ({ tasksApi: { list } }));
vi.mock("@/lib/api/dashboard", () => ({
  dashboardApi: { getStalledTasks: vi.fn(async () => []) },
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
    return null;
  },
}));

import TasksPage from "../page";

function wrapper(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <PageRefreshProvider>{ui}</PageRefreshProvider>
    </QueryClientProvider>
  );
}

describe("TasksPage — stuck filter", () => {
  beforeEach(() => {
    list.mockReset();
    capturedTaskTableProps.current = null;
  });

  it("sends the stuck param to the backend and lists exactly the returned set", async () => {
    urlParams = "stuck=1";
    list.mockResolvedValue([
      { id: "stuck-1", title: "Wedged", status: "in_progress", team: "backend" },
    ]);
    render(wrapper(<TasksPage />));

    await waitFor(() =>
      expect(capturedTaskTableProps.current?.tasks).toEqual([
        expect.objectContaining({ id: "stuck-1" }),
      ]),
    );
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ stuck: true }),
    );
  });

  it("does not send the stuck param when the filter is off", async () => {
    urlParams = "";
    list.mockResolvedValue([]);
    render(wrapper(<TasksPage />));

    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(list.mock.calls[0][0]).not.toHaveProperty("stuck");
  });
});
