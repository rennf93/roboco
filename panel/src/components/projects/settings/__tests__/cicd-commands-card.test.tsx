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

import { CicdCommandsCard } from "../cicd-commands-card";

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
    lint_command: "uv run ruff check .",
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

describe("CicdCommandsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("pre-fills the seven command fields from the project", () => {
    render(<CicdCommandsCard project={makeProject()} />);
    expect(screen.getByLabelText(/Lint Command/i)).toHaveValue(
      "uv run ruff check .",
    );
    expect(screen.getByLabelText(/Test Command/i)).toHaveValue("");
  });

  it("Save is disabled until a command changes", () => {
    render(<CicdCommandsCard project={makeProject()} />);
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Quality Gate Command/i), {
      target: { value: "make gate" },
    });
    expect(save).not.toBeDisabled();
  });

  it("saves all seven commands, omitting blanks", async () => {
    render(<CicdCommandsCard project={makeProject()} />);
    fireEvent.change(screen.getByLabelText(/Quality Gate Command/i), {
      target: { value: "make gate" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: Record<string, unknown>;
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates).toEqual({
      test_command: undefined,
      lint_command: "uv run ruff check .",
      format_command: undefined,
      typecheck_command: undefined,
      build_command: undefined,
      quality_command: "make gate",
      codegen_command: undefined,
    });
  });
});
