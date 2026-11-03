import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatus, Team, TaskType } from "@/types";

// The kanban board's list path is useTasks -> tasksApi.list -> GET
// /tasks/summary -> summaryToTask. The backend summary schema does not carry
// the stuck fields yet, so the wire is mocked here.
const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/mock-data", async (orig) => ({
  ...(await orig<typeof import("@/lib/mock-data")>()),
  isMockMode: () => false,
}));
vi.mock("../client", () => ({ default: { get } }));
vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { tasksApi } from "../tasks";
import { KanbanCard } from "@/components/kanban/core/kanban-card";
import { TooltipProvider } from "@/components/ui/tooltip";

const summaryRow = (overrides: Record<string, unknown> = {}) => ({
  id: "22222222-2222-2222-2222-222222222222",
  title: "Wedged task",
  status: TaskStatus.IN_PROGRESS,
  priority: 2,
  team: Team.BACKEND,
  assigned_to: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: null,
  task_type: TaskType.CODE,
  sequence: 0,
  description_snippet: null,
  ...overrides,
});

describe("tasksApi.list summary wire -> KanbanCard stuck chip", () => {
  beforeEach(() => get.mockReset());

  it("renders the stuck chip from summary-wire stuck fields", async () => {
    get.mockResolvedValue({
      data: [
        summaryRow({
          active_time_since_progress: 93900,
          open_wedge_strikes: 2,
          wedge_cycle: [
            {
              actor: "be-pm",
              verb: "triage",
              timestamp: "2026-11-01T01:00:00Z",
            },
          ],
        }),
      ],
    });

    const [task] = await tasksApi.list();
    expect(task.open_wedge_strikes).toBe(2);
    expect(task.wedge_cycle).toHaveLength(1);

    render(
      <TooltipProvider>
        <KanbanCard task={task} onAction={vi.fn()} showQaActions={false} />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("stuck-indicator")).toHaveTextContent(
      "stuck · 1d 2h since progress",
    );
  });

  it("maps blocker_resolver_type so a human-blocked card renders the chip", async () => {
    get.mockResolvedValue({
      data: [
        summaryRow({
          status: TaskStatus.BLOCKED,
          blocker_resolver_type: "human",
        }),
      ],
    });

    const [task] = await tasksApi.list();
    render(
      <TooltipProvider>
        <KanbanCard task={task} onAction={vi.fn()} showQaActions={false} />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("stuck-indicator")).toBeInTheDocument();
  });

  it("renders no chip for a healthy summary row", async () => {
    get.mockResolvedValue({ data: [summaryRow()] });

    const [task] = await tasksApi.list();
    render(
      <TooltipProvider>
        <KanbanCard task={task} onAction={vi.fn()} showQaActions={false} />
      </TooltipProvider>,
    );
    expect(screen.queryByTestId("stuck-indicator")).toBeNull();
  });
});
