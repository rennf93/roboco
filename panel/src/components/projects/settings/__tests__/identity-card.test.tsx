import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";
import { Team } from "@/types";
import type { Project } from "@/types";
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Radix Select's SelectValue sizing hook calls ResizeObserver, absent in
// jsdom — mirrors select-repo-picker.test.tsx's functional replacement.
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

const { useUpdateProject, mutateAsync } = vi.hoisted(() => ({
  useUpdateProject: vi.fn(),
  mutateAsync: vi.fn(),
}));
vi.mock("@/hooks/use-projects", () => ({ useUpdateProject }));

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

import { IdentityCard } from "../identity-card";

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

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function renderCard(project: Project) {
  return render(withQueryClient(<IdentityCard project={project} />));
}

describe("IdentityCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCredentialsStatus.mockResolvedValue({ has_credentials: true });
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders the slug (read-only), name, and git URL fields", () => {
    renderCard(makeProject());
    expect(screen.getByLabelText("Slug")).toHaveValue("roboco-api");
    expect(screen.getByLabelText("Slug")).toBeDisabled();
    expect(screen.getByLabelText(/Project Name/i)).toHaveValue("RoboCo API");
    expect(screen.getByLabelText(/Git URL/i)).toHaveValue(
      "https://github.com/org/repo.git",
    );
  });

  it("Save is disabled until a field is edited, then enables", () => {
    renderCard(makeProject());
    const save = screen.getByRole("button", { name: /^Save$/i });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Project Name/i), {
      target: { value: "Renamed" },
    });
    expect(save).not.toBeDisabled();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("saves the edited name and git URL, sending an explicit installation id", async () => {
    renderCard(makeProject());

    fireEvent.change(screen.getByLabelText(/Project Name/i), {
      target: { value: "Renamed API" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await vi.waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      projectId: string;
      updates: Record<string, unknown>;
    };
    expect(call.projectId).toBe("proj-1");
    expect(call.updates).toEqual({
      name: "Renamed API",
      git_url: "https://github.com/org/repo.git",
      git_provider: "github",
      github_installation_id: null,
    });
  });

  it("rejects an empty name or git URL without calling the mutation", () => {
    renderCard(makeProject());
    fireEvent.change(screen.getByLabelText(/Project Name/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    expect(toast.error).toHaveBeenCalledWith("Name and Git URL are required");
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("unbinding the GitHub App marks the card dirty and sends an explicit null", async () => {
    renderCard(makeProject({ github_installation_id: 42 }));

    fireEvent.click(await screen.findByRole("button", { name: /Unbind/i }));
    expect(screen.getByRole("button", { name: /^Save$/i })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await vi.waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { github_installation_id?: number | null };
    };
    expect(call.updates.github_installation_id).toBeNull();
  });

  it("warns when unbinding would leave the project with no credentials at all", async () => {
    renderCard(
      makeProject({ github_installation_id: 42, has_git_token: false }),
    );

    expect(
      screen.queryByText(/no GitHub App binding/i),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Unbind/i }));
    expect(screen.getByText(/no GitHub App binding/i)).toBeInTheDocument();
  });
});
