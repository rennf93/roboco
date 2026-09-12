import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { projectsApi, type ProjectFilters } from "@/lib/api/projects";
import type { ProjectCreate, ProjectUpdate } from "@/types";

// Query keys
export const projectKeys = {
  all: ["projects"] as const,
  lists: () => [...projectKeys.all, "list"] as const,
  list: (filters?: ProjectFilters) =>
    [...projectKeys.lists(), filters] as const,
  details: () => [...projectKeys.all, "detail"] as const,
  detail: (id: string) => [...projectKeys.details(), id] as const,
};

// Hooks
export function useProjects(filters?: ProjectFilters) {
  return useQuery({
    queryKey: projectKeys.list(filters),
    queryFn: () => projectsApi.list(filters),
    staleTime: 60000, // 1 minute
  });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: projectKeys.detail(projectId),
    queryFn: () => projectsApi.get(projectId),
    enabled: !!projectId,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (project: ProjectCreate) => projectsApi.create(project),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
    },
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      projectId,
      updates,
    }: {
      projectId: string;
      updates: ProjectUpdate;
    }) => projectsApi.update(projectId, updates),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
      queryClient.setQueryData(projectKeys.detail(project.id), project);
    },
  });
}

export function useSetWorkspace() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      projectId,
      localPath,
    }: {
      projectId: string;
      localPath: string;
    }) => projectsApi.setWorkspace(projectId, localPath),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
      queryClient.setQueryData(projectKeys.detail(project.id), project);
    },
  });
}

export function useDeactivateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (projectId: string) => projectsApi.deactivate(projectId),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
      queryClient.setQueryData(projectKeys.detail(project.id), project);
    },
  });
}

// Add an agent to a project's allowed-access list. The access routes respond
// with the updated ProjectResponse, so onSuccess seeds the detail cache with
// it and invalidates the detail to refetch (heals any server-side slug/name
// resolution drift) — the "refetch-on-success" the Access card relies on.
export function useGrantProjectAccess() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      projectId,
      agentId,
    }: {
      projectId: string;
      agentId: string;
    }) => projectsApi.grantAccess(projectId, agentId),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
      queryClient.setQueryData(projectKeys.detail(project.id), project);
      queryClient.invalidateQueries({ queryKey: projectKeys.detail(project.id) });
    },
  });
}

// Remove an agent from a project's allowed-access list (see
// useGrantProjectAccess for the cache/refetch posture).
export function useRevokeProjectAccess() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      projectId,
      agentId,
    }: {
      projectId: string;
      agentId: string;
    }) => projectsApi.revokeAccess(projectId, agentId),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
      queryClient.setQueryData(projectKeys.detail(project.id), project);
      queryClient.invalidateQueries({ queryKey: projectKeys.detail(project.id) });
    },
  });
}
