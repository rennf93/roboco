import { describe, it, expect, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { usePageRefresh } from "../use-page-refresh";
import { usePageRefresh as usePageRefreshPublic } from "@/hooks";
import { PageRefreshWrapper } from "@/components/providers/__tests__/test-utils";

describe("usePageRefresh", () => {
  it("throws when used outside a PageRefreshProvider", () => {
    expect(() => renderHook(() => usePageRefresh())).toThrow(
      "usePageRefresh must be used within a PageRefreshProvider",
    );
  });

  it("returns disabled=true and loading=false when nothing is registered", () => {
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });
    expect(result.current.disabled).toBe(true);
    expect(result.current.loading).toBe(false);
  });

  it("becomes enabled once a callback is registered, and disabled again once unregistered", () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });

    act(() => result.current.register(cb));
    expect(result.current.disabled).toBe(false);

    act(() => result.current.unregister(cb));
    expect(result.current.disabled).toBe(true);
  });

  it("registers and invokes callbacks on refresh", async () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });

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
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });

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

    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });
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

  it("does not run any callback when refresh is called with nothing registered", async () => {
    const cb = vi.fn();
    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });

    expect(result.current.disabled).toBe(true);

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

    const { result } = renderHook(() => usePageRefresh(), {
      wrapper: PageRefreshWrapper,
    });
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
