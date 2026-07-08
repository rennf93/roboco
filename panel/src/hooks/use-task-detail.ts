"use client";

import { useEffect } from "react";
import { useTask } from "@/hooks/use-tasks";
import { useProject } from "@/hooks/use-projects";
import { usePageRefresh } from "@/hooks/use-page-refresh";
import type { Task, Project } from "@/types";

export interface UseTaskDetailResult {
  task: Task | undefined;
  project: Project | undefined;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
}

/**
 * Fetches a single task and its owning project, and registers the task refetch
 * callback with the page-scoped refresh provider. The task detail page consumes
 * only `{ task, project, isLoading, error, refetch }` so it stays presentational
 * and avoids the `thin_components` convention warning.
 */
export function useTaskDetail(taskId: string): UseTaskDetailResult {
  const { data: task, isLoading, error, refetch } = useTask(taskId);

  const { data: project } = useProject(task?.project_id ?? "");

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  return {
    task,
    project,
    isLoading,
    error: error ?? null,
    refetch: () => void refetch(),
  };
}
