import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { tasksApi, type TaskFilters } from "@/lib/api/tasks";
import type {
  Task,
  TaskCreate,
  ProgressRequest,
  CheckpointRequest,
  CommitRequest,
  SoftBlockRequest,
  EscalateRequest,
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
  });
}

export function useSubtasks(parentTaskId: string) {
  const { data: allTasks = [] } = useTasks();

  return useQuery({
    queryKey: taskKeys.subtasks(parentTaskId),
    queryFn: async (): Promise<Task[]> => {
      // Filter tasks where parent_task_id matches
      return allTasks.filter((task) => task.parent_task_id === parentTaskId);
    },
    enabled: !!parentTaskId && allTasks.length > 0,
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
    mutationFn: ({ taskId, qaNotes }: { taskId: string; qaNotes?: string }) =>
      tasksApi.passQa(taskId, qaNotes),
    onSuccess: invalidateTask,
  });

  const failQa = useMutation({
    mutationFn: ({ taskId, qaNotes }: { taskId: string; qaNotes?: string }) =>
      tasksApi.failQa(taskId, qaNotes),
    onSuccess: invalidateTask,
  });

  const complete = useMutation({
    mutationFn: (taskId: string) => tasksApi.complete(taskId),
    onSuccess: invalidateTask,
  });

  const cancel = useMutation({
    mutationFn: (taskId: string) => tasksApi.cancel(taskId),
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
    mutationFn: (taskId: string) => tasksApi.docsComplete(taskId),
    onSuccess: invalidateTask,
  });

  const submitPmReview = useMutation({
    mutationFn: (taskId: string) => tasksApi.submitPmReview(taskId),
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
