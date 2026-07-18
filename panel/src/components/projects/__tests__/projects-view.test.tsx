import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Team } from "@/types";
import type { ProjectSummary } from "@/types";

vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => ({
    register: vi.fn(),
    unregister: vi.fn(),
    refresh: vi.fn(),
  }),
}));

const { useProjects } = vi.hoisted(() => ({ useProjects: vi.fn() }));
vi.mock("@/hooks/use-projects", () => ({ useProjects }));

vi.mock("../create-project-dialog", () => ({
  CreateProjectDialog: () => null,
}));

vi.mock("../project-card-grid", () => ({
  ProjectCardGrid: ({ projects }: { projects?: ProjectSummary[] }) => (
    <div data-testid="card-grid">
      {(projects ?? []).map((p) => p.name).join(",")}
    </div>
  ),
}));

vi.mock("../project-table", async () => {
  const actual = await vi.importActual<typeof import("../project-table")>(
    "../project-table",
  );
  return {
    teamLabels: actual.teamLabels,
    ProjectTable: ({ projects }: { projects?: ProjectSummary[] }) => (
      <div data-testid="table">
        {(projects ?? []).map((p) => p.name).join(",")}
      </div>
    ),
  };
});

import { ProjectsView } from "../projects-view";
import { useUIStore } from "@/store/ui-store";

function makeProject(overrides: Partial<ProjectSummary>): ProjectSummary {
  return {
    id: overrides.id ?? "p1",
    name: overrides.name ?? "Project",
    slug: overrides.slug ?? "project",
    git_url: "https://github.com/rennf93/roboco.git",
    assigned_cell: Team.BACKEND,
    is_active: true,
    has_workspace: true,
    has_git_token: true,
    video_engine_enabled: false,
    ci_watch_enabled: false,
    task_counts: null,
    ...overrides,
  };
}

const PROJECTS: ProjectSummary[] = [
  makeProject({ id: "p-zeta", name: "Zeta", assigned_cell: Team.FRONTEND }),
  makeProject({ id: "p-alpha", name: "Alpha", assigned_cell: Team.BACKEND }),
];

describe("ProjectsView", () => {
  beforeEach(() => {
    useUIStore.setState({ projectsView: "cards" });
    useProjects.mockReturnValue({
      data: PROJECTS,
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    });
  });

  it("defaults to the card grid view", () => {
    render(<ProjectsView />);
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
    expect(screen.queryByTestId("table")).not.toBeInTheDocument();
  });

  it("switches to the table view and back via the toggle", async () => {
    const user = userEvent.setup();
    render(<ProjectsView />);

    await user.click(screen.getByLabelText("Table view"));
    expect(screen.getByTestId("table")).toBeInTheDocument();
    expect(screen.queryByTestId("card-grid")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Card view"));
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
    expect(screen.queryByTestId("table")).not.toBeInTheDocument();
  });

  it("sorts cards by name ascending by default", () => {
    render(<ProjectsView />);
    expect(screen.getByTestId("card-grid")).toHaveTextContent("Alpha,Zeta");
  });

  it("flips to descending when the direction toggle is clicked", async () => {
    const user = userEvent.setup();
    render(<ProjectsView />);
    await user.click(screen.getByLabelText("Toggle sort direction"));
    expect(screen.getByTestId("card-grid")).toHaveTextContent("Zeta,Alpha");
  });
});
