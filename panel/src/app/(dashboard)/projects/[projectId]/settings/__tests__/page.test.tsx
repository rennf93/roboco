import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Team, type Project } from "@/types";

const mockPush = vi.fn();
const mockReplace = vi.fn();
const mockRouter = { push: mockPush, replace: mockReplace };
// Stable object per test (like real next/navigation — see
// a2a-view.test.tsx's identical note): an unstable mock would refire any
// effect keyed on it every render regardless of the real URL.
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => searchParams,
}));

const { useProject } = vi.hoisted(() => ({ useProject: vi.fn() }));
vi.mock("@/hooks/use-projects", () => ({ useProject }));

vi.mock("@/hooks", () => ({
  usePageRefresh: () => ({ register: vi.fn(), unregister: vi.fn() }),
}));

vi.mock("@/components/conventions/conventions-tab", () => ({
  ConventionsTab: ({ projectId }: { projectId: string }) => (
    <div data-testid="conventions-tab" data-project-id={projectId} />
  ),
}));

// Each card's own behavior (fields, dirty-state save, mutation payload) is
// covered by its dedicated test in settings/__tests__/*.test.tsx — this
// route test only cares that the page assembles all seven, so each is
// stubbed to a nameable marker.
vi.mock("@/components/projects/settings/identity-card", () => ({
  IdentityCard: ({ project }: { project: Project }) => (
    <div data-testid="identity-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/git-auth-card", () => ({
  GitAuthCard: ({ project }: { project: Project }) => (
    <div data-testid="git-auth-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/placement-card", () => ({
  PlacementCard: ({ project }: { project: Project }) => (
    <div data-testid="placement-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/environments-card", () => ({
  EnvironmentsCard: ({ project }: { project: Project }) => (
    <div data-testid="environments-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/cicd-commands-card", () => ({
  CicdCommandsCard: ({ project }: { project: Project }) => (
    <div data-testid="cicd-commands-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/budget-ops-card", () => ({
  BudgetOpsCard: ({ project }: { project: Project }) => (
    <div data-testid="budget-ops-card-stub" data-project-id={project.id} />
  ),
}));
vi.mock("@/components/projects/settings/sandbox-card", () => ({
  SandboxCard: ({ project }: { project: Project }) => (
    <div data-testid="sandbox-card-stub" data-project-id={project.id} />
  ),
}));

import ProjectSettingsPage from "../page";

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

async function renderPage() {
  await act(async () => {
    render(
      <ProjectSettingsPage params={Promise.resolve({ projectId: "proj-1" })} />,
    );
  });
}

describe("ProjectSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    searchParams = new URLSearchParams();
  });

  it("shows a skeleton while the project is loading", async () => {
    useProject.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      refetch: vi.fn(),
    });
    await renderPage();
    expect(screen.queryByText("RoboCo API")).not.toBeInTheDocument();
  });

  it("shows a not-found card when the project fails to load", async () => {
    useProject.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("404"),
      refetch: vi.fn(),
    });
    await renderPage();
    expect(screen.getByText("Project Not Found")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /View All Projects/i }),
    ).toBeInTheDocument();
  });

  it("renders the header (name + slug) and every settings card once loaded", async () => {
    useProject.mockReturnValue({
      data: makeProject(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    await renderPage();

    expect(
      screen.getByRole("heading", { name: "RoboCo API" }),
    ).toBeInTheDocument();
    expect(screen.getByText("roboco-api")).toBeInTheDocument();

    for (const testId of [
      "identity-card-stub",
      "git-auth-card-stub",
      "placement-card-stub",
      "environments-card-stub",
      "cicd-commands-card-stub",
      "budget-ops-card-stub",
      "sandbox-card-stub",
    ]) {
      expect(screen.getByTestId(testId)).toHaveAttribute(
        "data-project-id",
        "proj-1",
      );
    }
  });

  it("defaults to the Settings tab when the URL carries no ?tab", async () => {
    useProject.mockReturnValue({
      data: makeProject(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    await renderPage();

    expect(screen.getByRole("tab", { name: "Settings" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Conventions" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByTestId("identity-card-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("conventions-tab")).not.toBeInTheDocument();
  });

  it("activates the Conventions tab from ?tab=conventions", async () => {
    searchParams = new URLSearchParams("tab=conventions");
    useProject.mockReturnValue({
      data: makeProject(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    await renderPage();

    expect(screen.getByRole("tab", { name: "Conventions" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Settings" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
    expect(screen.getByTestId("conventions-tab")).toHaveAttribute(
      "data-project-id",
      "proj-1",
    );
    expect(screen.queryByTestId("identity-card-stub")).not.toBeInTheDocument();
  });
});
