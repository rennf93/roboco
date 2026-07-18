import api from "./client";
import { Team } from "@/types";
import type {
  Project,
  ProjectCreate,
  ProjectUpdate,
  ProjectSummary,
} from "@/types";
import { isMockMode } from "@/lib/mock-data";

// Mock-mode github.com detection: real host extraction (mirrors the backend's
// forge.py `_extract_host`) instead of a raw substring match, so a URL like
// "https://github.com.evil.tld/x/y.git" doesn't false-positive as github.
const detectGithubProvider = (gitUrl: string): "github" | null => {
  const url = gitUrl.trim();
  let host: string | null = null;
  if (url.includes("://")) {
    try {
      host = new URL(url).hostname.toLowerCase() || null;
    } catch {
      host = null;
    }
  } else {
    // scp-like SSH syntax: [user@]host:path (e.g. git@github.com:owner/repo.git)
    const match = /^(?:[^@/]+@)?([^/:]+):/.exec(url);
    host = match ? match[1].toLowerCase() : null;
  }
  return host === "github.com" ? "github" : null;
};

// Mock data for offline mode
const mockProjects: Project[] = [
  {
    id: "proj-mock-1",
    name: "roboco",
    slug: "roboco",
    git_url: "https://github.com/rennf93/roboco.git",
    git_provider: "github",
    default_branch: "master",
    protected_branches: ["master", "slave"],
    assigned_cell: Team.BACKEND,
    is_active: true,
    has_git_token: false,
    ci_watch_enabled: true,
    ci_watch_workflow: "CI",
    video_engine_enabled: false,
    workspace_path: "/data/workspaces/roboco",
    created_by: "ceo",
    created_at: "2026-06-01T00:00:00Z",
    updated_at: null,
  } as Project,
  {
    id: "proj-mock-2",
    name: "roboco-website",
    slug: "roboco-website",
    git_url: "https://github.com/rennf93/roboco-website.git",
    git_provider: "github",
    default_branch: "master",
    protected_branches: ["master"],
    assigned_cell: Team.FRONTEND,
    is_active: true,
    has_git_token: false,
    ci_watch_enabled: false,
    ci_watch_workflow: null,
    video_engine_enabled: false,
    workspace_path: null,
    created_by: "ceo",
    created_at: "2026-06-15T00:00:00Z",
    updated_at: null,
  } as Project,
];

// Mock task counts per project (mock-mode only — real data comes from the
// backend's grouped query). Keyed by project id.
const mockTaskCounts: Record<string, { done: number; active: number; blocked: number }> = {
  "proj-mock-1": { done: 120, active: 8, blocked: 1 },
  "proj-mock-2": { done: 34, active: 2, blocked: 0 },
};

export interface ProjectFilters {
  assigned_cell?: Team;
  active_only?: boolean;
  limit?: number;
  offset?: number;
}

export const projectsApi = {
  // List projects with optional filters
  list: async (filters?: ProjectFilters): Promise<ProjectSummary[]> => {
    if (isMockMode()) {
      let projects = [...mockProjects];
      if (filters?.assigned_cell) {
        projects = projects.filter(
          (p) => p.assigned_cell === filters.assigned_cell,
        );
      }
      if (filters?.active_only) {
        projects = projects.filter((p) => p.is_active);
      }
      return projects.map((p) => ({
        id: p.id,
        name: p.name,
        slug: p.slug,
        git_url: p.git_url,
        assigned_cell: p.assigned_cell,
        is_active: p.is_active,
        has_workspace: !!p.workspace_path,
        has_git_token: false, // Mock mode has no tokens
        video_engine_enabled: p.video_engine_enabled,
        ci_watch_enabled: !!p.ci_watch_enabled,
        task_counts: mockTaskCounts[p.id] ?? null,
      }));
    }

    const params = new URLSearchParams();
    if (filters?.assigned_cell)
      params.append("assigned_cell", filters.assigned_cell);
    if (filters?.active_only !== undefined)
      params.append("active_only", String(filters.active_only));
    if (filters?.limit) params.append("limit", String(filters.limit));
    if (filters?.offset) params.append("offset", String(filters.offset));

    const url = "/projects?" + params.toString();
    const { data } = await api.get<ProjectSummary[]>(url);
    return data;
  },

  // Get single project
  get: async (projectId: string): Promise<Project> => {
    if (isMockMode()) {
      const project = mockProjects.find((p) => p.id === projectId);
      if (!project) throw new Error("Project not found");
      return project;
    }

    const { data } = await api.get<Project>("/projects/" + projectId);
    return data;
  },

  // Create project (PM only)
  create: async (project: ProjectCreate): Promise<Project> => {
    if (isMockMode()) {
      const now = new Date().toISOString();
      const newProject: Project = {
        id: `project-${Date.now()}`,
        name: project.name,
        slug: project.slug,
        git_url: project.git_url,
        // Mock mode: mirror the backend's auto-detect (github.com -> github).
        git_provider:
          project.git_provider ??
          (project.git_url.includes("github.com") ? "github" : null),
        default_branch: project.default_branch ?? "main",
        environments: project.environments ?? null,
        protected_branches: project.protected_branches ?? ["main", "master"],
        assigned_cell: project.assigned_cell,
        has_git_token: !!project.git_token, // Mock token status
        is_active: true,
        test_command: project.test_command ?? null,
        lint_command: project.lint_command ?? null,
        format_command: project.format_command ?? null,
        typecheck_command: project.typecheck_command ?? null,
        build_command: project.build_command ?? null,
        quality_command: project.quality_command ?? null,
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
        created_by: "mock-user",
        created_at: now,
        updated_at: null,
      };
      mockProjects.push(newProject);
      return newProject;
    }
    const { data } = await api.post<Project>("/projects", project);
    return data;
  },

  // Update project (PM only)
  update: async (
    projectId: string,
    updates: ProjectUpdate,
  ): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = { ...mockProjects[idx], ...updates, updated_at: now };
      return mockProjects[idx];
    }
    const { data } = await api.patch<Project>(
      "/projects/" + projectId,
      updates,
    );
    return data;
  },

  // Set workspace path for local development
  setWorkspace: async (
    projectId: string,
    workspacePath: string,
  ): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = {
        ...mockProjects[idx],
        workspace_path: workspacePath,
        updated_at: now,
      };
      return mockProjects[idx];
    }
    const { data } = await api.post<Project>(
      "/projects/" + projectId + "/workspace",
      { workspace_path: workspacePath },
    );
    return data;
  },

  // Update sync state (for tracking git status)
  updateSyncState: async (
    projectId: string,
    headCommit: string,
  ): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = {
        ...mockProjects[idx],
        head_commit: headCommit,
        last_synced_at: now,
        updated_at: now,
      };
      return mockProjects[idx];
    }
    const { data } = await api.post<Project>(
      "/projects/" + projectId + "/sync-state",
      {
        head_commit: headCommit,
      },
    );
    return data;
  },

  // Deactivate project (soft delete)
  deactivate: async (projectId: string): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = {
        ...mockProjects[idx],
        is_active: false,
        updated_at: now,
      };
      return mockProjects[idx];
    }
    const { data } = await api.patch<Project>("/projects/" + projectId, {
      is_active: false,
    });
    return data;
  },

  // Delete project permanently
  delete: async (projectId: string): Promise<void> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx !== -1) mockProjects.splice(idx, 1);
      return;
    }
    await api.delete("/projects/" + projectId);
  },

  // Grant agent access to project
  grantAccess: async (projectId: string, agentId: string): Promise<void> => {
    if (isMockMode()) {
      return;
    }
    await api.post("/projects/" + projectId + "/access/" + agentId);
  },

  // Revoke agent access from project
  revokeAccess: async (projectId: string, agentId: string): Promise<void> => {
    if (isMockMode()) {
      return;
    }
    await api.delete("/projects/" + projectId + "/access/" + agentId);
  },

  // Trigger git sync for project
  sync: async (projectId: string): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = {
        ...mockProjects[idx],
        last_synced_at: now,
        updated_at: now,
      };
      return mockProjects[idx];
    }
    const { data } = await api.post<Project>(
      "/projects/" + projectId + "/sync",
    );
    return data;
  },
};
