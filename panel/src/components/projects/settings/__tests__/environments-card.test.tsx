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

import { EnvironmentsCard } from "../environments-card";
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

describe("EnvironmentsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders the empty-ladder state (0 rungs)", () => {
    render(<EnvironmentsCard project={makeProject()} />);
    expect(screen.getByText("0 rungs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Save$/i })).toBeDisabled();
  });

  it("adding a rung marks the card dirty and enables Save", () => {
    render(<EnvironmentsCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add rung/i }));
    expect(screen.getByText("1 rung")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Save$/i })).not.toBeDisabled();
  });

  it("saving an incomplete rung is rejected before the mutation fires", () => {
    render(<EnvironmentsCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add rung/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    expect(toast.error).toHaveBeenCalledWith(
      "Every environment rung needs both a name and a branch.",
    );
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("saves a completed ladder", async () => {
    render(<EnvironmentsCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add rung/i }));
    fireEvent.change(screen.getByPlaceholderText("Name (e.g. dev, qa, stag)"), {
      target: { value: "dev" },
    });
    fireEvent.change(screen.getByPlaceholderText("Branch (e.g. dev, master)"), {
      target: { value: "slave" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: { environments: unknown };
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates.environments).toEqual([
      { name: "dev", branch: "slave" },
    ]);
  });
});
