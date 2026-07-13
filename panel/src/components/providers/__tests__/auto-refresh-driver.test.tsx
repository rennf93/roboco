import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useState } from "react";
import { AutoRefreshDriver } from "../auto-refresh-driver";
import { PageRefreshProvider } from "../page-refresh-provider";
import { usePageRefresh } from "@/hooks";

// Non-reactive stand-in for the persisted UI store: AutoRefreshDriver reads it
// via the zustand selector form (`useUIStore((s) => s.foo)`), so the mock must
// accept and apply a selector. Mutate the fields directly and re-render to
// simulate a store update (matches the settings-page test idiom).
const mockStore = vi.hoisted(() => ({
  autoRefresh: false,
  refreshIntervalSeconds: 10,
}));

vi.mock("@/store", () => ({
  useUIStore: (selector: (s: typeof mockStore) => unknown) =>
    selector(mockStore),
}));

function Registrator({ callback }: { callback: () => void | Promise<void> }) {
  const { register } = usePageRefresh();
  const [registered, setRegistered] = useState(false);
  if (!registered) {
    register(callback);
    setRegistered(true);
  }
  return null;
}

function Harness({
  callback,
  mountRegistrator = true,
}: {
  callback: () => void | Promise<void>;
  mountRegistrator?: boolean;
}) {
  return (
    <PageRefreshProvider>
      {mountRegistrator && <Registrator callback={callback} />}
      <AutoRefreshDriver />
    </PageRefreshProvider>
  );
}

describe("AutoRefreshDriver", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockStore.autoRefresh = false;
    mockStore.refreshIntervalSeconds = 10;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("arms no interval when disabled (nothing registered), even if the pref is on", () => {
    mockStore.autoRefresh = true;
    render(<Harness callback={vi.fn()} mountRegistrator={false} />);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("arms no interval when Auto Refresh is off, even with a callback registered", () => {
    render(<Harness callback={vi.fn()} />);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("fires refresh at the configured interval when enabled + registered", async () => {
    mockStore.autoRefresh = true;
    const callback = vi.fn();
    render(<Harness callback={callback} />);

    // Async act so the refresh() promise chain (setLoading true -> ... ->
    // false) drains between ticks — otherwise the second tick's
    // loadingRef.current read races the still-pending microtask.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it("respects a changed interval", () => {
    mockStore.autoRefresh = true;
    mockStore.refreshIntervalSeconds = 5;
    const callback = vi.fn();
    render(<Harness callback={callback} />);

    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("stops ticking once the pref turns off (cleans up the interval)", () => {
    mockStore.autoRefresh = true;
    const callback = vi.fn();
    const { rerender } = render(<Harness callback={callback} />);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    mockStore.autoRefresh = false;
    rerender(<Harness callback={callback} />);
    expect(vi.getTimerCount()).toBe(0);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(callback).toHaveBeenCalledTimes(1); // unchanged
  });
});
