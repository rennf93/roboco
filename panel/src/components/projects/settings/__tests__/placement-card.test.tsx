import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
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

vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<(v: string) => void>(() => {});
  return {
    Select: ({
      onValueChange,
      children,
    }: {
      onValueChange?: (v: string) => void;
      children: React.ReactNode;
    }) => (
      <Ctx.Provider value={onValueChange ?? (() => {})}>
        {children}
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: React.ReactNode;
    }) => {
      const onValueChange = React.useContext(Ctx);
      return (
        <button type="button" onClick={() => onValueChange(value)}>
          {children}
        </button>
      );
    },
  };
});

import { PlacementCard } from "../placement-card";

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
    protected_branches: ["master", "slave"],
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

describe("PlacementCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders the existing protected branches as chips and the default branch", () => {
    render(<PlacementCard project={makeProject()} />);
    expect(screen.getByText("master")).toBeInTheDocument();
    expect(screen.getByText("slave")).toBeInTheDocument();
    expect(screen.getByLabelText(/Default Branch/i)).toHaveValue("main");
  });

  it("Save is disabled until a field changes", () => {
    render(<PlacementCard project={makeProject()} />);
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: /Active/i }));
    expect(save).not.toBeDisabled();
  });

  it("adding a protected branch marks the card dirty and saves the full list", async () => {
    render(<PlacementCard project={makeProject()} />);

    const input = screen.getByPlaceholderText(
      "Type a branch name, press Enter",
    );
    fireEvent.change(input, { target: { value: "release" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByText("release")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: Record<string, unknown>;
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates).toEqual({
      assigned_cell: "backend",
      default_branch: "main",
      protected_branches: ["master", "slave", "release"],
      is_active: true,
    });
  });

  it("toggling the cell select saves the new cell", async () => {
    render(<PlacementCard project={makeProject()} />);
    fireEvent.click(screen.getByText("Frontend"));
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { assigned_cell?: string };
    };
    expect(call.updates.assigned_cell).toBe("frontend");
  });

  it("blank default branch falls back to 'main' on save", async () => {
    render(<PlacementCard project={makeProject()} />);
    fireEvent.change(screen.getByLabelText(/Default Branch/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { default_branch?: string };
    };
    expect(call.updates.default_branch).toBe("main");
  });
});
