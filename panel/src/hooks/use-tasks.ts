import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { tasksApi, type TaskFilters } from "@/lib/api/tasks";
import {
  Team,
  TaskStatus,
  type Task,
  type TaskCreate,
  type ProgressRequest,
  type CheckpointRequest,
  type CommitRequest,
  type SoftBlockRequest,
  type EscalateRequest,
} from "@/types";

// Type for task updates - allows any Task field to be updated
export type TaskUpdate = Partial<Task>;

// Query keys
export const taskKeys = {
  all: ["tasks"] as const,
  lists: () => [...taskKeys.all, "list"] as const,
  list: (filters?: TaskFilters) => [...taskKeys.lists(), filters] as const,
  details: () => [...taskKeys.all, "detail"] as const,
  detail: (id: string) => [...taskKeys.details(), id] as const,
  subtasks: (parentId: string) => [...taskKeys.all, "subtasks", parentId] as const,
  boardReview: (id: string) => [...taskKeys.all, "board-review", id] as const,
  stats: () => [...taskKeys.all, "stats"] as const,
  statsByTeam: () => [...taskKeys.all, "stats-by-team"] as const,
};

// Hooks
export function useTasks(filters?: TaskFilters) {
  return useQuery({
    queryKey: taskKeys.list(filters),
    queryFn: () => tasksApi.list(filters),
    staleTime: 30000, // 30 seconds
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: taskKeys.detail(taskId),
    queryFn: () => tasksApi.get(taskId),
    enabled: !!taskId,
    // No per-task websocket exists, so while a board task is mid-review we poll
    // the detail so the "Approve & Start" button appears as soon as the board
    // finishes. Polling stops the moment board_review_complete flips true.
    refetchInterval: (query) =>
      query.state.data?.team === Team.BOARD &&
      !query.state.data?.board_review_complete
        ? 4000
        : false,
  });
}

// The board's review (PO + Head of Marketing) for a task. Enabled lazily so
// it's only fetched where it's shown (e.g. the approve/redraft surface).
export function useBoardReview(taskId: string, enabled = true) {
  return useQuery({
    queryKey: taskKeys.boardReview(taskId),
    queryFn: () => tasksApi.getBoardReview(taskId),
    enabled: !!taskId && enabled,
    staleTime: 30000,
  });
}

export function useSubtasks(parentTaskId: string) {
  return useQuery({
    queryKey: taskKeys.subtasks(parentTaskId),
    // Calls tasksApi.getSubtasks which hits GET /tasks/{id}/subtasks
    queryFn: () => tasksApi.getSubtasks(parentTaskId),
    enabled: !!parentTaskId,
  });
}

/**
 * Fetches valid next statuses for a task from GET /tasks/{id}/valid-transitions.
 * Returns undefined while loading; on error (including 404) gracefully returns
 * undefined so callers can fall back to a hardcoded map.
 */
export function useTaskValidTransitions(taskId: string) {
  return useQuery<TaskStatus[]>({
    queryKey: ["tasks", "valid-transitions", taskId] as const,
    queryFn: () => tasksApi.getValidTransitions(taskId),
    enabled: !!taskId,
    staleTime: 30000, // 30 seconds
    retry: false, // don't retry on 404 or other errors — caller falls back to hardcoded map
  });
}

export function useTaskStats() {
  return useQuery({
    queryKey: taskKeys.stats(),
    queryFn: () => tasksApi.getStats(),
    staleTime: 60000, // 1 minute
  });
}

export function useTaskStatsByTeam() {
  return useQuery({
    queryKey: taskKeys.statsByTeam(),
    queryFn: () => tasksApi.getStatsByTeam(),
    staleTime: 60000, // 1 minute
  });
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (task: TaskCreate) => tasksApi.create(task),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });
}

export function useUpdateTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ taskId, updates }: { taskId: string; updates: TaskUpdate }) =>
      tasksApi.update(taskId, updates),
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
      queryClient.setQueryData(taskKeys.detail(task.id), task);
    },
  });
}

export function useDeleteTask() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (taskId: string) => tasksApi.delete(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });
}

// Lifecycle action hooks
export function useTaskLifecycle() {
  const queryClient = useQueryClient();
  
  const invalidateTask = (task: Task) => {
    queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
    queryClient.setQueryData(taskKeys.detail(task.id), task);
  };

  const claim = useMutation({
    mutationFn: (taskId: string) => tasksApi.claim(taskId),
    onSuccess: invalidateTask,
  });

  const start = useMutation({
    mutationFn: (taskId: string) => tasksApi.start(taskId),
    onSuccess: invalidateTask,
  });

  const block = useMutation({
    mutationFn: ({ taskId, blockerId }: { taskId: string; blockerId?: string }) =>
      tasksApi.block(taskId, blockerId),
    onSuccess: invalidateTask,
  });

  const unblock = useMutation({
    mutationFn: (taskId: string) => tasksApi.unblock(taskId),
    onSuccess: invalidateTask,
  });

  const pause = useMutation({
    mutationFn: (taskId: string) => tasksApi.pause(taskId),
    onSuccess: invalidateTask,
  });

  const resume = useMutation({
    mutationFn: (taskId: string) => tasksApi.resume(taskId),
    onSuccess: invalidateTask,
  });

  const verify = useMutation({
    mutationFn: (taskId: string) => tasksApi.verify(taskId),
    onSuccess: invalidateTask,
  });

  const submitQa = useMutation({
    mutationFn: ({ taskId, devNotes }: { taskId: string; devNotes?: string }) =>
      tasksApi.submitQa(taskId, devNotes),
    onSuccess: invalidateTask,
  });

  const passQa = useMutation({
    mutationFn: ({ taskId, qaNotes }: { taskId: string; qaNotes: string }) =>
      tasksApi.passQa(taskId, qaNotes),
    onSuccess: invalidateTask,
  });

  const failQa = useMutation({
    mutationFn: ({ taskId, qaNotes }: { taskId: string; qaNotes: string }) =>
      tasksApi.failQa(taskId, qaNotes),
    onSuccess: invalidateTask,
  });

  const complete = useMutation({
    mutationFn: ({ taskId, justification }: { taskId: string; justification: string }) =>
      tasksApi.complete(taskId, justification),
    onSuccess: invalidateTask,
  });

  const cancel = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: string; reason: string }) =>
      tasksApi.cancel(taskId, reason),
    onSuccess: invalidateTask,
  });

  const reopen = useMutation({
    mutationFn: (taskId: string) => tasksApi.reopen(taskId),
    onSuccess: invalidateTask,
  });

  const activate = useMutation({
    mutationFn: (taskId: string) => tasksApi.activate(taskId),
    onSuccess: invalidateTask,
  });

  const docsComplete = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      tasksApi.docsComplete(taskId, notes),
    onSuccess: invalidateTask,
  });

  const submitPmReview = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      tasksApi.submitPmReview(taskId, notes),
    onSuccess: invalidateTask,
  });

  // Progress tracking
  const addProgress = useMutation({
    mutationFn: ({ taskId, request }: { taskId: string; request: ProgressRequest }) =>
      tasksApi.addProgress(taskId, request),
    onSuccess: invalidateTask,
  });

  const addCheckpoint = useMutation({
    mutationFn: ({ taskId, request }: { taskId: string; request: CheckpointRequest }) =>
      tasksApi.addCheckpoint(taskId, request),
    onSuccess: invalidateTask,
  });

  const addCommit = useMutation({
    mutationFn: ({ taskId, request }: { taskId: string; request: CommitRequest }) =>
      tasksApi.addCommit(taskId, request),
    onSuccess: invalidateTask,
  });

  // Soft block and escalation
  const softBlock = useMutation({
    mutationFn: ({ taskId, request }: { taskId: string; request: SoftBlockRequest }) =>
      tasksApi.softBlock(taskId, request),
    onSuccess: invalidateTask,
  });

  const escalate = useMutation({
    mutationFn: ({ taskId, request }: { taskId: string; request: EscalateRequest }) =>
      tasksApi.escalate(taskId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });

  // CEO Approval workflow
  const ceoApprove = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes?: string }) =>
      tasksApi.ceoApprove(taskId, notes),
    onSuccess: invalidateTask,
  });

  const ceoReject = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      tasksApi.ceoReject(taskId, notes),
    onSuccess: invalidateTask,
  });

  const escalateToCeo = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: string; reason: string }) =>
      tasksApi.escalateToCeo(taskId, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });

  // CEO gate #2: approve completed work and merge the PR.
  // Calls POST /tasks/{id}/approve-and-merge with no request body.
  const approveAndMerge = useMutation({
    mutationFn: (taskId: string) => tasksApi.approveAndMerge(taskId),
    onSuccess: invalidateTask,
  });

  return {
    // Lifecycle
    claim,
    start,
    block,
    unblock,
    pause,
    resume,
    verify,
    submitQa,
    passQa,
    failQa,
    complete,
    cancel,
    reopen,
    activate,
    docsComplete,
    submitPmReview,
    // Progress tracking
    addProgress,
    addCheckpoint,
    addCommit,
    // Soft block and escalation
    softBlock,
    escalate,
    // CEO Approval
    ceoApprove,
    ceoReject,
    escalateToCeo,
    approveAndMerge,
  };
}

// Query hook for tasks awaiting CEO approval
export function useTasksAwaitingCeoApproval() {
  return useQuery({
    queryKey: [...taskKeys.all, "awaiting-ceo"] as const,
    queryFn: () => tasksApi.getAwaitingCeoApproval(),
    staleTime: 30000,
  });
}

// Query hook for tasks awaiting PM review
export function useTasksAwaitingPmReview() {
  return useQuery({
    queryKey: [...taskKeys.all, "awaiting-pm-review"] as const,
    queryFn: () => tasksApi.getAwaitingPmReview(),
    staleTime: 30000,
  });
}
