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

// jsdom has no ResizeObserver; Radix Switch measures its thumb via one on
// mount — mirrors a2a-conversation-list.test.tsx's stub.
if (typeof window !== "undefined" && !window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

import { GitAuthCard } from "../git-auth-card";

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

describe("GitAuthCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("shows the token-set state and a Replace token input", () => {
    render(<GitAuthCard project={makeProject()} />);
    expect(screen.getByText("Token is set")).toBeInTheDocument();
    expect(screen.getByLabelText(/Replace token/i)).toBeInTheDocument();
  });

  it("shows the no-token state and a Set token input", () => {
    render(<GitAuthCard project={makeProject({ has_git_token: false })} />);
    expect(screen.getByText("No token configured")).toBeInTheDocument();
    expect(screen.getByLabelText(/Set token/i)).toBeInTheDocument();
  });

  it("Save is disabled until a token is entered or Clear is toggled", () => {
    render(<GitAuthCard project={makeProject()} />);
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Replace token/i), {
      target: { value: "ghp_new" },
    });
    expect(save).not.toBeDisabled();
  });

  it("saves a new token and resets the input on success", async () => {
    render(<GitAuthCard project={makeProject()} />);
    fireEvent.change(screen.getByLabelText(/Replace token/i), {
      target: { value: "ghp_newtoken" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: { git_token?: string };
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates).toEqual({ git_token: "ghp_newtoken" });
    await waitFor(() =>
      expect(screen.getByLabelText(/Replace token/i)).toHaveValue(""),
    );
  });

  it("clearing the token sends an explicit empty string", async () => {
    render(<GitAuthCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("switch", { name: /clear token/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { git_token?: string };
    };
    expect(call.updates).toEqual({ git_token: "" });
  });

  it("warns when clearing would leave the project with no credentials at all", () => {
    render(
      <GitAuthCard project={makeProject({ github_installation_id: null })} />,
    );
    expect(
      screen.queryByText(/no git credentials at all/i),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: /clear token/i }));
    expect(screen.getByText(/no git credentials at all/i)).toBeInTheDocument();
  });

  it("does not warn when a GitHub App binding already covers auth", () => {
    render(
      <GitAuthCard project={makeProject({ github_installation_id: 42 })} />,
    );
    fireEvent.click(screen.getByRole("switch", { name: /clear token/i }));
    expect(
      screen.queryByText(/no git credentials at all/i),
    ).not.toBeInTheDocument();
  });
});
