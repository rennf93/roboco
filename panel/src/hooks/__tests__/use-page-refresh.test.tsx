import { describe, it, expect, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { usePageRefresh } from "../use-page-refresh";
import { usePageRefresh as usePageRefreshPublic } from "@/hooks";
import { PageRefreshProvider } from "@/components/providers/page-refresh-provider";

function wrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}

function disabledWrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider disabled>{children}</PageRefreshProvider>;
}

describe("usePageRefresh", () => {
  it("throws when used outside a PageRefreshProvider", () => {
    expect(() => renderHook(() => usePageRefresh())).toThrow(
      "usePageRefresh must be used within a PageRefreshProvider",
    );
  });

  it("returns disabled=false and loading=false by default", () => {
    const { result } = renderHook(() => usePageRefresh(), { wrapper });
    expect(result.current.disabled).toBe(false);
    expect(result.current.loading).toBe(false);
  });

  it("reflects the provider disabled prop", () => {
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: disabledWrapper,
    });
    expect(result.current.disabled).toBe(true);
  });

  it("registers and invokes callbacks on refresh", async () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), { wrapper });

    act(() => {
      result.current.register(cb);
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("unregisters callbacks", async () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), { wrapper });

    act(() => {
      result.current.register(cb);
      result.current.unregister(cb);
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(cb).not.toHaveBeenCalled();
  });

  it("sets loading true while refresh is running and false after", async () => {
    let resolve: (() => void) | undefined;
    const deferred = new Promise<void>((r) => {
      resolve = r;
    });
    const cb = vi.fn(() => deferred);

    const { result } = renderHook(() => usePageRefresh(), { wrapper });
    act(() => result.current.register(cb));

    let refreshPromise: Promise<void>;
    act(() => {
      refreshPromise = result.current.refresh();
    });

    await waitFor(() => expect(result.current.loading).toBe(true));

    await act(async () => {
      resolve?.();
      await refreshPromise!;
    });

    expect(result.current.loading).toBe(false);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("does not run callbacks while disabled", async () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: disabledWrapper,
    });

    act(() => result.current.register(cb));

    await act(async () => {
      await result.current.refresh();
    });

    expect(cb).not.toHaveBeenCalled();
  });

  it("is exported publicly from the hooks barrel", () => {
    expect(usePageRefreshPublic).toBe(usePageRefresh);
  });

  it("does not start a second refresh while one is in progress", async () => {
    let resolve: (() => void) | undefined;
    const deferred = new Promise<void>((r) => {
      resolve = r;
    });
    const cb = vi.fn(() => deferred);

    const { result } = renderHook(() => usePageRefresh(), { wrapper });
    act(() => result.current.register(cb));

    let first: Promise<void>;
    let second: Promise<void>;
    act(() => {
      first = result.current.refresh();
      second = result.current.refresh();
    });

    act(() => resolve?.());
    await first!;
    await second!;

    expect(cb).toHaveBeenCalledTimes(1);
  });
});
