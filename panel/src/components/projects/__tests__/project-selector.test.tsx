import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { Team } from "@/types";
import type { ProjectSummary } from "@/types";

const { useProjects } = vi.hoisted(() => ({ useProjects: vi.fn() }));
vi.mock("@/hooks/use-projects", () => ({ useProjects }));

// Make the Select testable without Radix's portal/pointer machinery — mirrors
// a2a-reply-composer.test.tsx: SelectContent always renders its children, so
// the assertions below can query rendered project names directly.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: ({ placeholder }: { placeholder?: string }) => (
    <span>{placeholder}</span>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectGroup: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectLabel: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

import { ProjectSelector } from "../project-selector";

function project(overrides: Partial<ProjectSummary>): ProjectSummary {
  return {
    id: "p-id",
    name: "project",
    slug: "project",
    git_url: "https://example.com/project.git",
    assigned_cell: Team.FRONTEND,
    is_active: true,
    has_workspace: true,
    has_git_token: true,
    video_engine_enabled: false,
    ci_watch_enabled: false,
    task_counts: null,
    ...overrides,
  };
}

describe("ProjectSelector", () => {
  it("videoEngineOnly excludes projects that have not opted into the video engine", () => {
    useProjects.mockReturnValue({
      data: [
        project({ id: "p-1", name: "Video Ready", video_engine_enabled: true }),
        project({
          id: "p-2",
          name: "Not Opted In",
          video_engine_enabled: false,
        }),
      ],
      isLoading: false,
    });

    render(<ProjectSelector value={null} onChange={vi.fn()} videoEngineOnly />);

    expect(screen.getByText("Video Ready")).toBeInTheDocument();
    expect(screen.queryByText("Not Opted In")).not.toBeInTheDocument();
  });

  it("shows every project when videoEngineOnly is not set", () => {
    useProjects.mockReturnValue({
      data: [
        project({ id: "p-1", name: "Video Ready", video_engine_enabled: true }),
        project({
          id: "p-2",
          name: "Not Opted In",
          video_engine_enabled: false,
        }),
      ],
      isLoading: false,
    });

    render(<ProjectSelector value={null} onChange={vi.fn()} />);

    expect(screen.getByText("Video Ready")).toBeInTheDocument();
    expect(screen.getByText("Not Opted In")).toBeInTheDocument();
  });
});
