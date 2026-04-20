import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { workSessionsApi, type WorkSessionFilters } from "@/lib/api/work-sessions";
import type { WorkSession, WorkSessionCreate } from "@/types";

// Query keys
export const workSessionKeys = {
  all: ["work-sessions"] as const,
  lists: () => [...workSessionKeys.all, "list"] as const,
  list: (filters?: WorkSessionFilters) => [...workSessionKeys.lists(), filters] as const,
  details: () => [...workSessionKeys.all, "detail"] as const,
  detail: (id: string) => [...workSessionKeys.details(), id] as const,
  forTask: (taskId: string) => [...workSessionKeys.all, "task", taskId] as const,
};

// Hooks
export function useWorkSessions(filters?: WorkSessionFilters) {
  return useQuery({
    queryKey: workSessionKeys.list(filters),
    queryFn: () => workSessionsApi.list(filters),
    staleTime: 30000, // 30 seconds
  });
}

export function useWorkSession(sessionId: string) {
  return useQuery({
    queryKey: workSessionKeys.detail(sessionId),
    queryFn: () => workSessionsApi.get(sessionId),
    enabled: !!sessionId,
  });
}

export function useWorkSessionForTask(taskId: string) {
  return useQuery({
    queryKey: workSessionKeys.forTask(taskId),
    queryFn: () => workSessionsApi.getForTask(taskId),
    enabled: !!taskId,
    staleTime: 10000, // 10 seconds
  });
}

export function useCreateWorkSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (session: WorkSessionCreate) => workSessionsApi.create(session),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: workSessionKeys.lists() });
    },
  });
}

export function useWorkSessionActions() {
  const queryClient = useQueryClient();

  const invalidateSession = (session: WorkSession) => {
    queryClient.invalidateQueries({ queryKey: workSessionKeys.lists() });
    queryClient.setQueryData(workSessionKeys.detail(session.id), session);
    if (session.task_id) {
      queryClient.invalidateQueries({ queryKey: workSessionKeys.forTask(session.task_id) });
    }
  };

  const addCommit = useMutation({
    mutationFn: ({ sessionId, commitSha }: { sessionId: string; commitSha: string }) =>
      workSessionsApi.addCommit(sessionId, commitSha),
    onSuccess: invalidateSession,
  });

  const addFiles = useMutation({
    mutationFn: ({ sessionId, filePaths }: { sessionId: string; filePaths: string[] }) =>
      workSessionsApi.addFiles(sessionId, filePaths),
    onSuccess: invalidateSession,
  });

  const createPR = useMutation({
    mutationFn: ({
      sessionId,
      prNumber,
      prUrl,
    }: {
      sessionId: string;
      prNumber: number;
      prUrl: string;
    }) => workSessionsApi.createPR(sessionId, prNumber, prUrl),
    onSuccess: invalidateSession,
  });

  const updatePRStatus = useMutation({
    mutationFn: ({ sessionId, prStatus }: { sessionId: string; prStatus: string }) =>
      workSessionsApi.updatePRStatus(sessionId, prStatus),
    onSuccess: invalidateSession,
  });

  const mergePR = useMutation({
    mutationFn: ({ sessionId, mergedBy }: { sessionId: string; mergedBy: string }) =>
      workSessionsApi.mergePR(sessionId, mergedBy),
    onSuccess: invalidateSession,
  });

  const complete = useMutation({
    mutationFn: (sessionId: string) => workSessionsApi.complete(sessionId),
    onSuccess: invalidateSession,
  });

  const abandon = useMutation({
    mutationFn: ({ sessionId, reason }: { sessionId: string; reason?: string }) =>
      workSessionsApi.abandon(sessionId, reason),
    onSuccess: invalidateSession,
  });

  return {
    addCommit,
    addFiles,
    createPR,
    updatePRStatus,
    mergePR,
    complete,
    abandon,
  };
}
