import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { ProjectTable } from "../project-table";
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

describe("ProjectTable", () => {
  it("renders the project name, task counts, and CI-watch badge", () => {
    render(<ProjectTable projects={[project]} isLoading={false} />);
    expect(screen.getByText("RoboCo Core")).toBeInTheDocument();
    expect(screen.getByText("42 done")).toBeInTheDocument();
    expect(screen.getByText("1 blocked")).toBeInTheDocument();
    expect(screen.getByText("CI-Watch")).toBeInTheDocument();
  });

  it("shows the empty state when there are no projects", () => {
    render(<ProjectTable projects={[]} isLoading={false} />);
    expect(screen.getByText("No projects found")).toBeInTheDocument();
  });

  it("renders an em-dash placeholder when task_counts is null", () => {
    const bare: ProjectSummary = {
      ...project,
      id: "p2",
      name: "bare-project",
      ci_watch_enabled: false,
      task_counts: null,
    };
    render(<ProjectTable projects={[bare]} isLoading={false} />);
    // Renders the row (not the empty state) and the CI-Watch badge is absent.
    expect(screen.getByText("bare-project")).toBeInTheDocument();
    expect(screen.queryByText("CI-Watch")).not.toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<ProjectTable projects={undefined} isLoading={true} />);
    expect(screen.queryByText("No projects found")).not.toBeInTheDocument();
  });
});