import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";
import { Team } from "@/types";
import type { Project } from "@/types";
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

  it("hides the picker for an auto-detected gitlab.com URL (git_provider stored as null)", async () => {
    renderDialog(
      makeProject({
        git_provider: null,
        git_url: "https://gitlab.com/acme/widgets.git",
      }),
    );

    expect(
      await screen.findByText(/App auth is GitHub-only/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Select repo/i }),
    ).not.toBeInTheDocument();
  });

  it("warns when saving would clear both the App binding and the PAT", async () => {
    renderDialog(
      makeProject({ github_installation_id: null, has_git_token: true }),
    );

    expect(
      screen.queryByText(/no git credentials at all/i),
    ).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("switch", { name: /clear token/i }));

    expect(
      await screen.findByText(/no git credentials at all/i),
    ).toBeInTheDocument();
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

describe("EditProjectDialog — Protected Branches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCredentialsStatus.mockResolvedValue({ has_credentials: true });
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  it("renders the project's existing protected branches as chips", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));

    await screen.findByText("master");
    expect(screen.getByText("slave")).toBeInTheDocument();
  });

  it("adds a branch via Enter and removes another via its chip, saving both changes", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    // Remove "slave".
    fireEvent.click(screen.getByLabelText("Remove slave"));
    expect(screen.queryByText("slave")).not.toBeInTheDocument();

    // Add "release" by typing + Enter.
    const input = screen.getByPlaceholderText(
      "Type a branch name, press Enter",
    );
    fireEvent.change(input, { target: { value: "release" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByText("release")).toBeInTheDocument();
    // The input clears after a successful add.
    expect(input).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { protected_branches?: string[] };
    };
    expect(call.updates.protected_branches).toEqual(["master", "release"]);
  });

  it("adding via a trailing comma also commits the chip", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    const input = screen.getByPlaceholderText(
      "Type a branch name, press Enter",
    );
    fireEvent.change(input, { target: { value: "hotfix" } });
    fireEvent.keyDown(input, { key: "," });
    expect(screen.getByText("hotfix")).toBeInTheDocument();
  });

  it("pasting a comma-separated list splits it into individual chips instead of one malformed chip", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    const input = screen.getByPlaceholderText(
      "Type a branch name, press Enter",
    );
    fireEvent.paste(input, {
      clipboardData: { getData: () => "release,hotfix,staging" },
    });

    expect(screen.getByText("release")).toBeInTheDocument();
    expect(screen.getByText("hotfix")).toBeInTheDocument();
    expect(screen.getByText("staging")).toBeInTheDocument();
    expect(
      screen.queryByText("release,hotfix,staging"),
    ).not.toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("does not add a duplicate chip for a branch already in the list", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    const input = screen.getByPlaceholderText(
      "Type a branch name, press Enter",
    );
    fireEvent.change(input, { target: { value: "master" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getAllByText("master")).toHaveLength(1);
  });

  it("clearing every branch sends an explicit empty array, not an omitted field", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    fireEvent.click(screen.getByLabelText("Remove master"));
    fireEvent.click(screen.getByLabelText("Remove slave"));
    expect(screen.queryByText("master")).not.toBeInTheDocument();
    expect(screen.queryByText("slave")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { protected_branches?: string[] };
    };
    expect(call.updates.protected_branches).toEqual([]);
  });

  it("leaving the list untouched still round-trips the same branches on save", async () => {
    renderDialog(makeProject({ protected_branches: ["master", "slave"] }));
    await screen.findByText("master");

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { protected_branches?: string[] };
    };
    expect(call.updates.protected_branches).toEqual(["master", "slave"]);
  });
});

describe("EditProjectDialog — Monthly Budget (USD)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCredentialsStatus.mockResolvedValue({ has_credentials: true });
    mutateAsync.mockResolvedValue(makeProject());
    useUpdateProject.mockReturnValue({ mutateAsync, isPending: false });
  });

  function openAutonomySection() {
    fireEvent.click(
      screen.getByRole("button", { name: /Show Autonomous Maintenance/i }),
    );
  }

  // fireEvent.submit(form) rather than clicking the Save button — this
  // dialog's Tabs-wrapped form doesn't reliably translate a button click
  // into a submit event under jsdom; submitting the form directly is the
  // same idiom create-task-dialog.test.tsx already uses.
  function submit() {
    fireEvent.submit(document.querySelector("form")!);
  }

  it("pre-fills the stored monthly_budget_usd", async () => {
    renderDialog(makeProject({ monthly_budget_usd: 42 }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    expect(screen.getByLabelText(/Monthly Budget/i)).toHaveValue(42);
  });

  it("rejects 0 with an inline error and does not submit", async () => {
    renderDialog(makeProject({ monthly_budget_usd: null }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "0" },
    });
    submit();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/greater than 0/i),
      );
    });
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("rejects a negative budget the same way", async () => {
    renderDialog(makeProject({ monthly_budget_usd: null }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "-5" },
    });
    submit();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringMatching(/greater than 0/i),
      );
    });
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("submits null when cleared (no cap)", async () => {
    renderDialog(makeProject({ monthly_budget_usd: 42 }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "" },
    });
    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { monthly_budget_usd?: number | null };
    };
    expect(call.updates.monthly_budget_usd).toBeNull();
  });

  it("submits a positive cap as a number", async () => {
    renderDialog(makeProject({ monthly_budget_usd: null }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    fireEvent.change(screen.getByLabelText(/Monthly Budget/i), {
      target: { value: "100" },
    });
    submit();

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    const call = mutateAsync.mock.calls[0][0] as {
      updates: { monthly_budget_usd?: number | null };
    };
    expect(call.updates.monthly_budget_usd).toBe(100);
  });

  it("shows this month's spend against the cap when monthly_spend_usd is present", async () => {
    renderDialog(
      makeProject({ monthly_budget_usd: 100, monthly_spend_usd: 42.5 }),
    );
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    expect(screen.getByTestId("project-spend").textContent).toBe(
      "Spent: $42.50 this month / $100.00",
    );
  });

  it("hides the ratio (but still shows spend) when there is no monthly cap", async () => {
    renderDialog(makeProject({ monthly_budget_usd: null, monthly_spend_usd: 10 }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    expect(screen.getByTestId("project-spend").textContent).toBe(
      "Spent: $10.00 this month",
    );
  });

  it("hides the spend line entirely when monthly_spend_usd is absent (flag off)", async () => {
    renderDialog(makeProject({ monthly_budget_usd: 100, monthly_spend_usd: null }));
    await screen.findByRole("button", { name: /Save Changes/i });
    openAutonomySection();

    expect(screen.queryByTestId("project-spend")).toBeNull();
  });
});
