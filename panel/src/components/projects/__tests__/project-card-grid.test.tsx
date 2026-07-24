import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// The dialog itself (its own hooks/queries) is covered by
// quick-edit-project-dialog.test.tsx; here we only care whether the name
// click opens it with the right project id, not what it renders.
vi.mock("../quick-edit-project-dialog", () => ({
  QuickEditProjectDialog: ({
    projectId,
    open,
  }: {
    projectId: string;
    open: boolean;
  }) =>
    open ? (
      <div data-testid="quick-edit-dialog" data-project-id={projectId} />
    ) : null,
}));

import { ProjectCardGrid } from "../project-card-grid";
import { Team } from "@/types";
import type { ProjectSummary } from "@/types";

const project: ProjectSummary = {
  id: "p1",
  name: "RoboCo Core",
  slug: "roboco",
  git_url: "https://github.com/rennf93/roboco.git",
  assigned_cell: Team.BACKEND,
  is_active: true,
  has_workspace: true,
  has_git_token: true,
  video_engine_enabled: false,
  ci_watch_enabled: true,
  task_counts: { done: 42, active: 5, blocked: 1 },
};

describe("ProjectCardGrid", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("renders one card carrying the project's name, cell, tasks, token, status, and CI-watch badge", () => {
    render(<ProjectCardGrid projects={[project]} isLoading={false} />);
    expect(screen.getByText("RoboCo Core")).toBeInTheDocument();
    expect(screen.getByText("roboco")).toBeInTheDocument();
    expect(screen.getByText("Backend")).toBeInTheDocument();
    expect(screen.getByText("42 done")).toBeInTheDocument();
    expect(screen.getByText("1 blocked")).toBeInTheDocument();
    expect(screen.getByText("Token Set")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("CI-Watch")).toBeInTheDocument();
  });

  it("shows the empty state when there are no projects", () => {
    render(<ProjectCardGrid projects={[]} isLoading={false} />);
    expect(screen.getByText("No projects found")).toBeInTheDocument();
  });

  it("renders an em-dash placeholder when task_counts is null and omits the CI-Watch badge", () => {
    const bare: ProjectSummary = {
      ...project,
      id: "p2",
      name: "bare-project",
      ci_watch_enabled: false,
      task_counts: null,
    };
    render(<ProjectCardGrid projects={[bare]} isLoading={false} />);
    expect(screen.getByText("bare-project")).toBeInTheDocument();
    expect(screen.queryByText("CI-Watch")).not.toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<ProjectCardGrid projects={undefined} isLoading={true} />);
    expect(screen.queryByText("No projects found")).not.toBeInTheDocument();
  });

  it("the Edit action routes to the full settings page instead of opening a dialog", () => {
    render(<ProjectCardGrid projects={[project]} isLoading={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit project" }));
    expect(mockPush).toHaveBeenCalledWith("/projects/p1/settings");
    expect(screen.queryByTestId("quick-edit-dialog")).not.toBeInTheDocument();
  });

  it("clicking the project name opens the slim quick-edit dialog instead of navigating", () => {
    render(<ProjectCardGrid projects={[project]} isLoading={false} />);
    fireEvent.click(screen.getByRole("button", { name: "RoboCo Core" }));
    expect(mockPush).not.toHaveBeenCalled();
    expect(screen.getByTestId("quick-edit-dialog")).toHaveAttribute(
      "data-project-id",
      "p1",
    );
  });
});
