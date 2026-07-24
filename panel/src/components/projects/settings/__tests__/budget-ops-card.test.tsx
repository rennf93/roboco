import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Team } from "@/types";
import type { Project } from "@/types";

const { useUpdateProject, mutateAsync } = vi.hoisted(() => ({
  useUpdateProject: vi.fn(),
  mutateAsync: vi.fn(),
}));
vi.mock("@/hooks/use-projects", () => ({ useUpdateProject }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

if (typeof window !== "undefined" && !window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

import { BudgetOpsCard } from "../budget-ops-card";
import { toast } from "sonner";

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "proj-1",
    name: "RoboCo API",
    slug: "roboco-api",
    git_url: "https://github.com/org/repo.git",
    git_provider: "github",
    github_installation_id: null,
    default_branch: "main",
    environments: null,
    protected_branches: ["main"],
    assigned_cell: Team.BACKEND,
    has_git_token: true,
    is_active: true,
    test_command: null,
    lint_command: null,
    format_command: null,
    typecheck_command: null,
    build_command: null,
    quality_command: null,
    codegen_command: null,
    ci_watch_enabled: false,
    ci_watch_workflow: null,
    video_engine_enabled: false,
    dep_update_command: null,
    dep_update_paths: null,
    monthly_budget_usd: null,
    sandbox_services: null,
    sandbox_extensions: null,
    workspace_path: null,
    last_synced_at: null,
    head_commit: null,
    created_by: "ceo",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

describe("BudgetOpsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("pre-fills the stored monthly budget and shows spend against it", () => {
    render(
      <BudgetOpsCard
        project={makeProject({
          monthly_budget_usd: 100,
          monthly_spend_usd: 42.5,
        })}
      />,
    );
    expect(screen.getByLabelText(/Monthly Budget/i)).toHaveValue(100);
    expect(screen.getByTestId("project-spend").textContent).toBe(
      "Spent: $42.50 this month / $100.00",
    );
  });

  it("Save is disabled until a field changes", () => {
    render(<BudgetOpsCard project={makeProject()} />);
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: /CI-watch/i }));
    expect(save).not.toBeDisabled();
  });

  it("rejects a 0 or negative budget without saving", () => {
    render(<BudgetOpsCard project={makeProject()} />);
    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    expect(toast.error).toHaveBeenCalledWith(
      expect.stringMatching(/greater than 0/i),
    );
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("saves an explicit null when the budget is cleared", async () => {
    render(<BudgetOpsCard project={makeProject({ monthly_budget_usd: 42 })} />);
    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: Record<string, unknown>;
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates.monthly_budget_usd).toBeNull();
  });

  it("saves the CI-watch, video-engine, and dep-update fields together", async () => {
    render(<BudgetOpsCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("switch", { name: /CI-watch/i }));
    fireEvent.change(screen.getByLabelText(/CI-watch Workflow/i), {
      target: { value: "ci.yml" },
    });
    fireEvent.click(screen.getByRole("switch", { name: /Video engine/i }));
    fireEvent.change(screen.getByLabelText(/Dependency-Update Command/i), {
      target: { value: "uv lock --upgrade" },
    });
    fireEvent.change(
      screen.getByLabelText(/Dependency-Update Lockfile Paths/i),
      {
        target: { value: "uv.lock, pnpm-lock.yaml" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: Record<string, unknown>;
    };
    expect(call.updates).toEqual({
      monthly_budget_usd: null,
      ci_watch_enabled: true,
      ci_watch_workflow: "ci.yml",
      video_engine_enabled: true,
      dep_update_command: "uv lock --upgrade",
      dep_update_paths: ["uv.lock", "pnpm-lock.yaml"],
    });
  });
});
