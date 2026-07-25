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

import { SandboxCard } from "../sandbox-card";

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

describe("SandboxCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders the three service toggles, none checked by default", () => {
    render(<SandboxCard project={makeProject()} />);
    expect(
      screen.getByRole("switch", { name: "PostgreSQL" }),
    ).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Redis" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "MongoDB" })).not.toBeChecked();
    expect(screen.queryByText("PostgreSQL Extensions")).not.toBeInTheDocument();
  });

  it("enabling postgres reveals its extension toggles and marks the card dirty", () => {
    render(<SandboxCard project={makeProject()} />);
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: "PostgreSQL" }));
    expect(screen.getByText("PostgreSQL Extensions")).toBeInTheDocument();
    expect(save).not.toBeDisabled();
  });

  it("saves the enabled services and their picked extensions", async () => {
    render(<SandboxCard project={makeProject()} />);
    fireEvent.click(screen.getByRole("switch", { name: "PostgreSQL" }));
    fireEvent.click(screen.getByRole("switch", { name: "pgvector" }));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: { sandbox_services?: string[]; sandbox_extensions?: unknown };
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates.sandbox_services).toEqual(["postgres"]);
    expect(call.updates.sandbox_extensions).toEqual({ postgres: ["vector"] });
  });

  it("pre-checks services and extensions already stored on the project", () => {
    render(
      <SandboxCard
        project={makeProject({
          sandbox_services: ["redis"],
          sandbox_extensions: { redis: ["search"] },
        })}
      />,
    );
    expect(screen.getByRole("switch", { name: "Redis" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "RediSearch" })).toBeChecked();
    expect(screen.getByRole("button", { name: /^Save$/i })).toBeDisabled();
  });
});
