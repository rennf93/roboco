import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useGitBrowser } from "../use-git-browser";

const {
  mockUseProjects,
  mockUseGitStatus,
  mockUseGitLog,
  mockUseGitBranches,
  mockUseGitDiff,
  mockUseGitOperations,
  mockUsePageRefresh,
  mockUseSearchParams,
  mockUseRouter,
  mockToastSuccess,
  mockToastError,
} = vi.hoisted(() => ({
  mockUseProjects: vi.fn(),
  mockUseGitStatus: vi.fn(),
  mockUseGitLog: vi.fn(),
  mockUseGitBranches: vi.fn(),
  mockUseGitDiff: vi.fn(),
  mockUseGitOperations: vi.fn(),
  mockUsePageRefresh: vi.fn(),
  mockUseSearchParams: vi.fn(),
  mockUseRouter: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockToastError: vi.fn(),
}));

vi.mock("@/hooks/use-projects", () => ({
  useProjects: () => mockUseProjects(),
}));

vi.mock("@/hooks/use-git", () => ({
  useGitStatus: (...args: unknown[]) => mockUseGitStatus(...args),
  useGitLog: (...args: unknown[]) => mockUseGitLog(...args),
  useGitBranches: (...args: unknown[]) => mockUseGitBranches(...args),
  useGitDiff: (...args: unknown[]) => mockUseGitDiff(...args),
  useGitOperations: () => mockUseGitOperations(),
}));

vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => mockUsePageRefresh(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockUseSearchParams(),
  useRouter: () => mockUseRouter(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: mockToastSuccess,
    error: mockToastError,
    warning: vi.fn(),
  },
}));

vi.mock("@/lib/api/client", () => ({
  getErrorMessage: (err: unknown) =>
    err instanceof Error ? err.message : "Unknown error",
}));

function buildMutations(overrides: Record<string, unknown> = {}) {
  return {
    commit: { mutateAsync: vi.fn(), isPending: false },
    push: { mutateAsync: vi.fn(), isPending: false },
    createBranch: { mutateAsync: vi.fn(), isPending: false },
    checkout: { mutateAsync: vi.fn(), isPending: false },
    createPR: { mutateAsync: vi.fn(), isPending: false },
    mergePR: { mutateAsync: vi.fn(), isPending: false },
    pull: { mutateAsync: vi.fn(), isPending: false },
    fetch: { mutateAsync: vi.fn(), isPending: false },
    rebase: { mutateAsync: vi.fn(), isPending: false },
    ...overrides,
  };
}

function buildQueryResult(data: unknown) {
  return {
    data,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  };
}

describe("useGitBrowser", () => {
  const registeredCallbacks: Array<() => void | Promise<void>> = [];

  beforeEach(() => {
    vi.clearAllMocks();
    registeredCallbacks.length = 0;

    mockUseProjects.mockReturnValue(buildQueryResult([]));
    mockUseGitStatus.mockReturnValue(buildQueryResult(null));
    mockUseGitLog.mockReturnValue(buildQueryResult(null));
    mockUseGitBranches.mockReturnValue(buildQueryResult(null));
    mockUseGitDiff.mockReturnValue(buildQueryResult(null));
    mockUseGitOperations.mockReturnValue(buildMutations());
    mockUsePageRefresh.mockReturnValue({
      register: (cb: () => void) => registeredCallbacks.push(cb),
      unregister: (cb: () => void) => {
        const idx = registeredCallbacks.indexOf(cb);
        if (idx >= 0) registeredCallbacks.splice(idx, 1);
      },
      refresh: vi.fn(),
      loading: false,
    });
    mockUseSearchParams.mockReturnValue(
      new URLSearchParams("project=roboco&task=t1"),
    );
    mockUseRouter.mockReturnValue({ push: vi.fn() });
  });

  it("reads project and task ids from URL search params", () => {
    const { result } = renderHook(() => useGitBrowser());
    expect(result.current.projectSlug).toBe("roboco");
    expect(result.current.taskId).toBe("t1");
  });

  it("passes project slug and enabled flag to git query hooks", () => {
    renderHook(() => useGitBrowser());

    expect(mockUseGitStatus).toHaveBeenCalledWith("roboco", "t1", true);
    expect(mockUseGitLog).toHaveBeenCalledWith("roboco", 20, undefined, true);
    expect(mockUseGitBranches).toHaveBeenCalledWith("roboco", true, true);
    expect(mockUseGitDiff).toHaveBeenCalledWith(
      "roboco",
      true,
      undefined,
      true,
    );
    expect(mockUseGitDiff).toHaveBeenCalledWith(
      "roboco",
      false,
      undefined,
      true,
    );
  });

  it("disables git query hooks when no project is selected", () => {
    mockUseSearchParams.mockReturnValue(new URLSearchParams());
    renderHook(() => useGitBrowser());

    expect(mockUseGitStatus).toHaveBeenCalledWith("", "", false);
    expect(mockUseGitLog).toHaveBeenCalledWith("", 20, undefined, false);
    expect(mockUseGitBranches).toHaveBeenCalledWith("", true, false);
  });

  it("registers refetch callbacks for projects, status, log and branches", () => {
    const refetchProjects = vi.fn();
    const refetchStatus = vi.fn();
    const refetchLog = vi.fn();
    const refetchBranches = vi.fn();

    mockUseProjects.mockReturnValue(buildQueryResult([]));
    mockUseProjects.mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: refetchProjects,
    });
    mockUseGitStatus.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
      refetch: refetchStatus,
    });
    mockUseGitLog.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
      refetch: refetchLog,
    });
    mockUseGitBranches.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
      refetch: refetchBranches,
    });

    renderHook(() => useGitBrowser());

    expect(registeredCallbacks.length).toBe(4);
    registeredCallbacks.forEach((cb) => cb());

    expect(refetchProjects).toHaveBeenCalledTimes(1);
    expect(refetchStatus).toHaveBeenCalledTimes(1);
    expect(refetchLog).toHaveBeenCalledTimes(1);
    expect(refetchBranches).toHaveBeenCalledTimes(1);
  });

  it("updates URL when project selection changes", () => {
    const push = vi.fn();
    mockUseRouter.mockReturnValue({ push });

    const { result } = renderHook(() => useGitBrowser());
    result.current.handleProjectChange("other");

    expect(push).toHaveBeenCalledWith("/git?project=other");
  });

  it("clears task param and removes project param when selection is empty", () => {
    const push = vi.fn();
    mockUseRouter.mockReturnValue({ push });
    mockUseSearchParams.mockReturnValue(
      new URLSearchParams("project=roboco&task=t1"),
    );

    const { result } = renderHook(() => useGitBrowser());
    result.current.handleProjectChange("");

    expect(push).toHaveBeenCalledWith("/git");
  });

  it("commits with the current project/task and shows a success toast", async () => {
    const mutateAsync = vi.fn(() =>
      Promise.resolve({ commit_hash: "abc1234" }),
    );
    mockUseGitOperations.mockReturnValue(
      buildMutations({ commit: { mutateAsync, isPending: false } }),
    );

    const { result } = renderHook(() => useGitBrowser());
    await result.current.handleCommit("fix: something");

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        project_slug: "roboco",
        message: "fix: something",
        task_id: "t1",
        agent_id: "ceo",
      }),
    );
    expect(mockToastSuccess).toHaveBeenCalledWith("Committed: abc1234");
  });

  it("creates a PR and shows a success toast with the PR url", async () => {
    const mutateAsync = vi.fn(() =>
      Promise.resolve({
        pr_number: 42,
        pr_url: "https://github.com/x/y/pull/42",
      }),
    );
    mockUseGitOperations.mockReturnValue(
      buildMutations({ createPR: { mutateAsync, isPending: false } }),
    );

    const { result } = renderHook(() => useGitBrowser());
    await result.current.handleCreatePR("title", "body");

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        project_slug: "roboco",
        task_id: "t1",
        title: "title",
        body: "body",
        agent_id: "ceo",
      }),
    );
    expect(mockToastSuccess).toHaveBeenCalledWith(
      "Created PR #42: https://github.com/x/y/pull/42",
    );
  });

  it("shows an error toast when a git operation fails", async () => {
    const mutateAsync = vi.fn(() => Promise.reject(new Error("nope")));
    mockUseGitOperations.mockReturnValue(
      buildMutations({ pull: { mutateAsync, isPending: false } }),
    );

    const { result } = renderHook(() => useGitBrowser());
    await result.current.handlePull();

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith("Failed to pull from remote"),
    );
  });

  it("surfaces a rebase conflict warning instead of an error", async () => {
    const mutateAsync = vi.fn(() =>
      Promise.resolve({ conflict: true, conflicted_files: ["foo.ts"] }),
    );
    mockUseGitOperations.mockReturnValue(
      buildMutations({ rebase: { mutateAsync, isPending: false } }),
    );

    const { result } = renderHook(() => useGitBrowser());
    await result.current.handleRebase("main");

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        project_slug: "roboco",
        target_branch: "main",
        task_id: "t1",
        agent_id: "ceo",
      }),
    );
  });

  it("exposes pending flags from git operation mutations", () => {
    mockUseGitOperations.mockReturnValue(
      buildMutations({
        commit: { mutateAsync: vi.fn(), isPending: true },
        push: { mutateAsync: vi.fn(), isPending: false },
      }),
    );

    const { result } = renderHook(() => useGitBrowser());
    expect(result.current.isCommitting).toBe(true);
    expect(result.current.isPushing).toBe(false);
  });
});
