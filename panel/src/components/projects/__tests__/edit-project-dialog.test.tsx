import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";
import { Team } from "@/types";
import type { Project } from "@/types";

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
// (SelectItem wired to onValueChange via context) so the dialog's Forge /
// Assigned Cell selects mount without crashing.
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

const {
  getCredentialsStatus,
  listInstallations,
  listInstallationRepositories,
} = vi.hoisted(() => ({
  getCredentialsStatus: vi.fn(async () => ({ has_credentials: true })),
  listInstallations: vi.fn(async () => [{ id: 42, account_login: "acme" }]),
  listInstallationRepositories: vi.fn(async () => [
    {
      full_name: "acme/widgets",
      clone_url: "https://github.com/acme/widgets.git",
      private: false,
    },
  ]),
}));
vi.mock("@/lib/api", () => ({
  githubAppApi: {
    getCredentialsStatus,
    listInstallations,
    listInstallationRepositories,
  },
}));

import { EditProjectDialog } from "../edit-project-dialog";

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

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function renderDialog(project: Project) {
  useProject.mockReturnValue({ data: project, isLoading: false });
  return render(
    withQueryClient(
      <EditProjectDialog projectId={project.id} open onOpenChange={vi.fn()} />,
    ),
  );
}

describe("EditProjectDialog — GitHub App binding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCredentialsStatus.mockResolvedValue({ has_credentials: true });
    listInstallations.mockResolvedValue([{ id: 42, account_login: "acme" }]);
    listInstallationRepositories.mockResolvedValue([
      {
        full_name: "acme/widgets",
        clone_url: "https://github.com/acme/widgets.git",
        private: false,
      },
    ]);
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("shows the PAT state and a repo picker when the App is configured", async () => {
    renderDialog(makeProject());

    expect(
      await screen.findByText("Using personal access token"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Select repo/i }),
    ).toBeInTheDocument();
  });

  it("shows the current binding and an Unbind button when already bound", async () => {
    renderDialog(makeProject({ github_installation_id: 42 }));

    expect(
      await screen.findByText("Using GitHub App (installation #42)"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Unbind/i })).toBeInTheDocument();
  });

  it("shows a muted note and no picker when the App is not configured", async () => {
    getCredentialsStatus.mockResolvedValue({ has_credentials: false });
    renderDialog(makeProject());

    expect(
      await screen.findByText(/Configure the GitHub App/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Select repo/i }),
    ).not.toBeInTheDocument();
  });

  it("hides the picker for a non-GitHub forge provider", async () => {
    renderDialog(makeProject({ git_provider: "gitlab" }));

    expect(
      await screen.findByText(/App auth is GitHub-only/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Select repo/i }),
    ).not.toBeInTheDocument();
  });

  it("binding via the repo picker sets github_installation_id in the submitted payload", async () => {
    renderDialog(makeProject());

    const pickButton = await screen.findByRole("button", {
      name: /Select repo/i,
    });
    fireEvent.click(pickButton);
    fireEvent.click(await screen.findByText("acme/widgets"));

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { github_installation_id?: number | null };
    };
    expect(call.updates.github_installation_id).toBe(42);
  });

  it("unbinding sends an explicit null so the backend clears the stored installation", async () => {
    renderDialog(makeProject({ github_installation_id: 42 }));

    fireEvent.click(await screen.findByRole("button", { name: /Unbind/i }));
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { github_installation_id?: number | null };
    };
    expect(call.updates.github_installation_id).toBeNull();
  });
});
