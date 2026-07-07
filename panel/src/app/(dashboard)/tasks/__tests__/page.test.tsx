import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { list } = vi.hoisted(() => ({
  list: vi.fn(async () => []),
}));

vi.mock("@/lib/api/tasks", () => ({ tasksApi: { list } }));

vi.mock("next/navigation", () => {
  const params = new URLSearchParams("status=completed&team=backend");
  return {
    useSearchParams: () => params,
    useRouter: () => ({ push: vi.fn() }),
  };
});

vi.mock("@/hooks/use-projects", () => ({
  useProjects: () => ({ data: [] }),
}));

vi.mock("@/hooks/use-products", () => ({
  useProducts: () => ({ data: [] }),
}));

vi.mock("@/components/tasks", () => ({
  CreateTaskDialog: () => null,
  TaskFilters: () => null,
  TaskTable: () => null,
}));

import TasksPage from "../page";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("TasksPage — passes status/team/limit server-side (H17)", () => {
  beforeEach(() => {
    list.mockReset();
    list.mockResolvedValue([]);
  });

  it("forwards single status + team + limit=500 to tasksApi.list", async () => {
    render(withQueryClient(<TasksPage />));
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
