import api from "./client";
import type {
  Project,
  ProjectCreate,
  ProjectUpdate,
  ProjectSummary,
  Team,
} from "@/types";
import { isMockMode } from "@/lib/mock-data";

// Mock data for offline mode
const mockProjects: Project[] = [];

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
        projects = projects.filter((p) => p.assigned_cell === filters.assigned_cell);
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
        has_git_token: false,  // Mock mode has no tokens
      }));
    }

    const params = new URLSearchParams();
    if (filters?.assigned_cell) params.append("assigned_cell", filters.assigned_cell);
    if (filters?.active_only !== undefined) params.append("active_only", String(filters.active_only));
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
        default_branch: project.default_branch ?? "main",
        protected_branches: project.protected_branches ?? ["main", "master"],
        assigned_cell: project.assigned_cell,
        has_git_token: !!project.git_token,  // Mock token status
        is_active: true,
        test_command: project.test_command ?? null,
        lint_command: project.lint_command ?? null,
        format_command: project.format_command ?? null,
        typecheck_command: project.typecheck_command ?? null,
        build_command: project.build_command ?? null,
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
  update: async (projectId: string, updates: ProjectUpdate): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = { ...mockProjects[idx], ...updates, updated_at: now };
      return mockProjects[idx];
    }
    const { data } = await api.patch<Project>("/projects/" + projectId, updates);
    return data;
  },

  // Set workspace path for local development
  setWorkspace: async (projectId: string, workspacePath: string): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = { ...mockProjects[idx], workspace_path: workspacePath, updated_at: now };
      return mockProjects[idx];
    }
    const { data } = await api.post<Project>("/projects/" + projectId + "/workspace", { workspace_path: workspacePath });
    return data;
  },

  // Update sync state (for tracking git status)
  updateSyncState: async (
    projectId: string,
    headCommit: string
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
    const { data } = await api.post<Project>("/projects/" + projectId + "/sync-state", {
      head_commit: headCommit,
    });
    return data;
  },

  // Deactivate project (soft delete)
  deactivate: async (projectId: string): Promise<Project> => {
    if (isMockMode()) {
      const idx = mockProjects.findIndex((p) => p.id === projectId);
      if (idx === -1) throw new Error("Project not found");
      const now = new Date().toISOString();
      mockProjects[idx] = { ...mockProjects[idx], is_active: false, updated_at: now };
      return mockProjects[idx];
    }
    const { data } = await api.patch<Project>("/projects/" + projectId, { is_active: false });
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
    const { data } = await api.post<Project>("/projects/" + projectId + "/sync");
    return data;
  },
};
