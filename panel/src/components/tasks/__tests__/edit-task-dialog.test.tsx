import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { mutateAsync, spendState } = vi.hoisted(() => ({
  mutateAsync: vi.fn().mockResolvedValue(undefined),
  // Mutable per-test stand-in for useTask's query result — mirrors the
  // real hook's shape ({ data }) so the dialog's spend read-out can be
  // exercised without a real fetch.
  spendState: { data: undefined as { spend_usd?: number | null } | undefined },
}));

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync, isPending: false }),
  useTask: () => spendState,
}));

// useProducts: one product so the Product <Select> has a pickable option,
// mirroring create-task-dialog's test mock.
vi.mock("@/hooks/use-products", () => ({
  useProducts: () => ({
    data: [{ id: "prod-1", name: "Fan-out Prod" }],
  }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/components/agents/agent-selector", () => ({
  AgentSelector: () => null,
}));
vi.mock("@/components/projects/project-selector", () => ({
  ProjectSelector: () => null,
}));
vi.mock("../markdown-editor", () => ({ MarkdownEditor: () => null }));
vi.mock("../acceptance-criteria-editor", () => ({
  AcceptanceCriteriaEditor: () => null,
}));

// Collapsible: always render children open, so the Budget field (inside
// Advanced Options) is reachable without simulating the toggle click.
vi.mock("@/components/ui/collapsible", () => ({
  Collapsible: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  CollapsibleTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  CollapsibleContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

// Select: a native <select> stub, matching create-task-dialog's convention.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange?: (v: string) => void;
    children: React.ReactNode;
  }) => (
    <select value={value} onChange={(e) => onValueChange?.(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectItem: ({
    value,
    children,
  }: {
    value: string;
    children: React.ReactNode;
  }) => <option value={value}>{children}</option>,
}));

import { EditTaskDialog } from "../edit-task-dialog";
import { mockTasks } from "@/lib/mock-data";
import { toast } from "sonner";

const task = mockTasks[0];

function submit() {
  fireEvent.submit(document.querySelector("form")!);
}

function budgetInput(): HTMLInputElement {
  return screen.getByPlaceholderText("Task-type default") as HTMLInputElement;
}

describe("EditTaskDialog — Budget (USD) input", () => {
  beforeEach(() => {
    mutateAsync.mockClear();
    spendState.data = undefined;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty when the task has no budget_usd", () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: null }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(budgetInput().value).toBe("");
  });

  it("pre-fills the stored budget_usd", () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: 3.5 }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(budgetInput().value).toBe("3.5");
  });

  it("rejects 0 with an inline error and does not submit", async () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: null }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(budgetInput(), { target: { value: "0" } });
    submit();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/greater than 0/i),
      );
    });
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("rejects a negative budget the same way", async () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: null }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(budgetInput(), { target: { value: "-1" } });
    submit();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/greater than 0/i),
      );
    });
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("submits null when left empty (use the task-type default)", async () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: 3.5 }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(budgetInput(), { target: { value: "" } });
    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const { updates } = mutateAsync.mock.calls[0][0] as {
      updates: Record<string, unknown>;
    };
    expect(updates.budget_usd).toBeNull();
  });

  it("submits a positive budget as a number", async () => {
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: null }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(budgetInput(), { target: { value: "2.5" } });
    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const { updates } = mutateAsync.mock.calls[0][0] as {
      updates: Record<string, unknown>;
    };
    expect(updates.budget_usd).toBe(2.5);
  });
});

describe("EditTaskDialog — spend read-out", () => {
  beforeEach(() => {
    mutateAsync.mockClear();
    spendState.data = undefined;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders spend against the cap once useTask resolves", () => {
    spendState.data = { spend_usd: 12.34 };
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: 20 }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("task-spend").textContent).toBe(
      "Spent: $12.34 / $20.00",
    );
  });

  it("hides the ratio (but still shows spend) when there is no budget cap", () => {
    spendState.data = { spend_usd: 5 };
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: null }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("task-spend").textContent).toBe("Spent: $5.00");
  });

  it("renders nothing while the spend fetch hasn't resolved yet", () => {
    spendState.data = undefined;
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: 20 }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("task-spend")).toBeNull();
  });

  it("renders nothing when the task-budgets flag is off (spend_usd null)", () => {
    spendState.data = { spend_usd: null };
    render(
      <EditTaskDialog
        task={{ ...task, budget_usd: 20 }}
        open={true}
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("task-spend")).toBeNull();
  });
});
