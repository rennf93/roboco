import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Team } from "@/types";
import type { Project } from "@/types";
import type { BoardProgram } from "@/lib/api/board-programs";

const { useUpdateProject, mutateAsync } = vi.hoisted(() => ({
  useUpdateProject: vi.fn(),
  mutateAsync: vi.fn(),
}));
vi.mock("@/hooks/use-projects", () => ({ useUpdateProject }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { listBoardPrograms } = vi.hoisted(() => ({
  // Empty by default so the pre-existing suites below (none of which test
  // this section) render with no participate/exclude checkboxes; the Board
  // Programs describe block overrides this per test.
  listBoardPrograms: vi.fn(async (): Promise<BoardProgram[]> => []),
}));
vi.mock("@/lib/api/board-programs", () => ({
  boardProgramsApi: { list: listBoardPrograms },
}));

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
    board_programs: null,
    workspace_path: null,
    last_synced_at: null,
    head_commit: null,
    created_by: "ceo",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

function buildProgram(overrides: Partial<BoardProgram> = {}): BoardProgram {
  return {
    key: "pest_control",
    title: "Pest Control",
    description: "Weekly bug hunt over the opted-in project.",
    role: "product_owner",
    trigger: "cron",
    scope: "project",
    enabled: true,
    opted_in_project_slugs: [],
    last_opened_at: null,
    open_cycle: false,
    last_cycle_summary: null,
    ...overrides,
  };
}

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function renderCard(project: Project) {
  return render(withQueryClient(<BudgetOpsCard project={project} />));
}

describe("BudgetOpsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
    listBoardPrograms.mockResolvedValue([]);
  });

  it("pre-fills the stored monthly budget and shows spend against it", () => {
    renderCard(
      makeProject({
        monthly_budget_usd: 100,
        monthly_spend_usd: 42.5,
      }),
    );
    expect(screen.getByLabelText(/Monthly Budget/i)).toHaveValue(100);
    expect(screen.getByTestId("project-spend").textContent).toBe(
      "Spent: $42.50 this month / $100.00",
    );
  });

  it("Save is disabled until a field changes", () => {
    renderCard(makeProject());
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: /CI-watch/i }));
    expect(save).not.toBeDisabled();
  });

  it("rejects a 0 or negative budget without saving", () => {
    renderCard(makeProject());
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
    renderCard(makeProject({ monthly_budget_usd: 42 }));
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
    renderCard(makeProject());
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
      board_programs: [],
    });
  });
});

describe("BudgetOpsCard — Board Programs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
    listBoardPrograms.mockResolvedValue([
      buildProgram({ key: "pest_control", scope: "project" }),
      buildProgram({
        key: "roadmap",
        title: "Roadmap Cycle",
        scope: "org",
        role: "product_owner",
      }),
    ]);
  });

  it("renders a project-scoped program as a participates-in checkbox", async () => {
    renderCard(makeProject({ board_programs: null }));

    expect(
      await screen.findByText("Board Programs — participates in"),
    ).toBeInTheDocument();
    expect(screen.getByText("Pest Control")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Pest Control" }),
    ).not.toBeChecked();
  });

  it("renders an org-scoped program as an excluded-from checkbox", async () => {
    renderCard(makeProject({ board_programs: null }));

    expect(
      await screen.findByText("Board Programs — excluded from"),
    ).toBeInTheDocument();
    expect(screen.getByText("Roadmap Cycle")).toBeInTheDocument();
  });

  it("pre-checks a project-scoped checkbox already in the stored list", async () => {
    renderCard(makeProject({ board_programs: ["pest_control"] }));

    expect(
      await screen.findByRole("switch", { name: "Pest Control" }),
    ).toBeChecked();
  });

  it("pre-checks an org-scoped exclusion checkbox already in the stored list", async () => {
    renderCard(makeProject({ board_programs: ["!roadmap"] }));

    expect(
      await screen.findByText("Board Programs — excluded from"),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Roadmap Cycle" })).toBeChecked();
  });

  it("toggling participates-in and excluded-from checkboxes submits both entries", async () => {
    renderCard(makeProject({ board_programs: null }));

    fireEvent.click(
      await screen.findByRole("switch", { name: "Pest Control" }),
    );
    fireEvent.click(screen.getByRole("switch", { name: "Roadmap Cycle" }));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { board_programs?: string[] };
    };
    expect(new Set(call.updates.board_programs)).toEqual(
      new Set(["pest_control", "!roadmap"]),
    );
  });

  it("saving an unrelated field round-trips untouched Board Programs unchanged", async () => {
    renderCard(makeProject({ board_programs: ["!roadmap"] }));
    await screen.findByText("Board Programs — excluded from");

    fireEvent.click(screen.getByRole("switch", { name: /CI-watch/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { board_programs?: string[] };
    };
    expect(call.updates.board_programs).toEqual(["!roadmap"]);
  });
});
