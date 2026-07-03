import { describe, it, expect, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useIsMobile } from "../use-is-mobile";

// Minimal matchMedia stub — jsdom doesn't implement it. Tracks listeners so
// tests can flip `matches` and fire a synthetic "change" event.
//
// Note on SSR/hydration safety: useIsMobile is built on useSyncExternalStore
// with a `getServerSnapshot` that always returns `false`. React uses that same
// value for the server render AND the client's first (pre-hydration-commit)
// render, so there is no mismatch to reproduce here — RTL's `renderHook` only
// ever does a client render, so it exercises `getSnapshot` (the real
// matchMedia read), never the server path. That guarantee is structural
// (React's contract for the hook), not something a jsdom unit test observes.
function installMatchMedia(initialMatches: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: initialMatches,
    media: query,
    addEventListener: (_: "change", cb: (e: MediaQueryListEvent) => void) => {
      listeners.add(cb);
    },
    removeEventListener: (
      _: "change",
      cb: (e: MediaQueryListEvent) => void,
    ) => {
      listeners.delete(cb);
    },
  })) as unknown as typeof window.matchMedia;

  return {
    fireChange(matches: boolean) {
      initialMatches = matches;
      listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent));
    },
  };
}

describe("useIsMobile", () => {
  const originalMatchMedia = window.matchMedia;

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    vi.restoreAllMocks();
  });

  it("resolves to the real matchMedia value on render", () => {
    installMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("resolves false when the query does not match", () => {
    installMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("reacts to a live matchMedia change (viewport resize)", () => {
    const { fireChange } = installMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => {
      fireChange(true);
    });
    expect(result.current).toBe(true);
  });

  it("builds the query from a custom breakpoint", () => {
    installMatchMedia(false);
    renderHook(() => useIsMobile(1024));
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 1023px)");
  });

  it("defaults to the md breakpoint (768px) when none is passed", () => {
    installMatchMedia(false);
    renderHook(() => useIsMobile());
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 767px)");
  });
});
