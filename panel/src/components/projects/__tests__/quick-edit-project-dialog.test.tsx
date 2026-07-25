import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";
import { Team } from "@/types";
import type { Project } from "@/types";
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// jsdom has no ResizeObserver; Radix Switch (the always-rendered "Active"
// toggle) measures its thumb via one on mount — mirrors
// a2a-conversation-list.test.tsx's stub.
if (typeof window !== "undefined" && !window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// Radix Select's SelectValue sizing hook calls ResizeObserver, absent in
// jsdom — mirrors select-repo-picker.test.tsx's functional replacement
// (SelectItem wired to onValueChange via context) so the Assigned Cell
// select mounts without crashing.
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

const { useProject, useUpdateProject, mutateAsync } = vi.hoisted(() => ({
  useProject: vi.fn(),
  useUpdateProject: vi.fn(),
  mutateAsync: vi.fn(),
}));
vi.mock("@/hooks/use-projects", () => ({ useProject, useUpdateProject }));

import { QuickEditProjectDialog } from "../quick-edit-project-dialog";

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

function renderDialog(project: Project, onOpenChange = vi.fn()) {
  useProject.mockReturnValue({ data: project, isLoading: false });
  return render(
    <QuickEditProjectDialog
      projectId={project.id}
      open
      onOpenChange={onOpenChange}
    />,
  );
}

describe("QuickEditProjectDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders only name, assigned cell, and active — no git/token/CI-CD fields", () => {
    renderDialog(makeProject());

    expect(screen.getByLabelText(/Project Name/i)).toBeInTheDocument();
    expect(screen.getByText(/Assigned Cell/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Active/i)).toBeInTheDocument();

    expect(screen.queryByLabelText(/Git URL/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Set token|Replace token/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Lint Command/i)).not.toBeInTheDocument();
  });

  it("saves the three fields on submit", async () => {
    renderDialog(makeProject());

    fireEvent.change(screen.getByLabelText(/Project Name/i), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByText("Frontend"));
    fireEvent.click(screen.getByRole("switch", { name: /Active/i }));
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: Record<string, unknown>;
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates).toEqual({
      name: "Renamed",
      assigned_cell: "frontend",
      is_active: false,
    });
  });

  it("requires a name before submitting", () => {
    renderDialog(makeProject());
    fireEvent.change(screen.getByLabelText(/Project Name/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    expect(toast.error).toHaveBeenCalledWith(
      "Please fill in all required fields",
    );
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("cancel closes without saving", () => {
    const onOpenChange = vi.fn();
    renderDialog(makeProject(), onOpenChange);
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("'Full settings' closes the dialog and navigates to the settings page", () => {
    const onOpenChange = vi.fn();
    renderDialog(makeProject(), onOpenChange);
    fireEvent.click(screen.getByRole("button", { name: /Full settings/i }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mockPush).toHaveBeenCalledWith("/projects/proj-1/settings");
  });

  it("shows a loading skeleton while the project query is in flight", () => {
    useProject.mockReturnValue({ data: undefined, isLoading: true });
    render(
      <QuickEditProjectDialog projectId="proj-1" open onOpenChange={vi.fn()} />,
    );
    expect(screen.queryByLabelText(/Project Name/i)).not.toBeInTheDocument();
  });
});
