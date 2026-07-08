import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useContext, type ReactNode } from "react";
import { PageRefreshProvider } from "@/components/page-refresh-provider";
import {
  PageRefreshContext,
  type PageRefreshContextValue,
} from "@/store/page-refresh-context";

function usePageRefreshContextValue(): PageRefreshContextValue {
  const context = useContext(PageRefreshContext);
  if (!context) {
    throw new Error("PageRefreshContext not provided");
  }
  return context;
}

function wrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}

describe("PageRefreshProvider", () => {
  it("exposes null active scope by default", () => {
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    expect(result.current.activeScope).toBeNull();
  });

  it("registers and triggers a refresh callback for the active scope", async () => {
    const callback = vi.fn();
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    act(() => {
      result.current.register("dashboard", callback);
      result.current.setActiveScope("dashboard");
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("triggers the callback for an explicit scope", async () => {
    const callback = vi.fn();
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    act(() => {
      result.current.register("tasks", callback);
    });

    await act(async () => {
      await result.current.refresh("tasks");
    });

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("does not call a callback after it is unregistered", async () => {
    const callback = vi.fn();
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    act(() => {
      result.current.register("agents", callback);
      result.current.setActiveScope("agents");
      result.current.unregister("agents");
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(callback).not.toHaveBeenCalled();
  });

  it("does nothing when no active scope is set and no explicit scope is passed", async () => {
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.activeScope).toBeNull();
  });

  it("updates active scope through setActiveScope", () => {
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    act(() => {
      result.current.setActiveScope("insights");
    });

    expect(result.current.activeScope).toBe("insights");

    act(() => {
      result.current.setActiveScope(null);
    });

    expect(result.current.activeScope).toBeNull();
  });

  it("allows multiple scopes to be registered independently", async () => {
    const dashboardCallback = vi.fn();
    const tasksCallback = vi.fn();
    const { result } = renderHook(() => usePageRefreshContextValue(), {
      wrapper,
    });

    act(() => {
      result.current.register("dashboard", dashboardCallback);
      result.current.register("tasks", tasksCallback);
      result.current.setActiveScope("dashboard");
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(dashboardCallback).toHaveBeenCalledTimes(1);
    expect(tasksCallback).not.toHaveBeenCalled();

    act(() => {
      result.current.setActiveScope("tasks");
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(dashboardCallback).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(tasksCallback).toHaveBeenCalledTimes(1));
  });
});
