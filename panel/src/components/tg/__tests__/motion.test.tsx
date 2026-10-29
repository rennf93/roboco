import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { act, renderHook } from "@testing-library/react";
import { TgSheet, useCountUp } from "../motion";

describe("useCountUp", () => {
  it("jumps straight to the target under reduced motion", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    try {
      const { result } = renderHook(() => useCountUp(42));
      await waitFor(() => expect(result.current).toBe(42));
    } finally {
      window.matchMedia = original;
    }
  });

  it("settles on the exact target when animating", async () => {
    const { result } = renderHook(() => useCountUp(12.34, 50));
    await waitFor(() => expect(result.current).toBe(12.34));
  });

  it("counts up on the frame clock even when it lags performance.now()", () => {
    // Frames are driven by hand on a clock 1.2 s behind performance.now(),
    // the lag jsdom shows: the count must still finish after its duration
    // and never dip below where it started.
    const frames: FrameRequestCallback[] = [];
    const raf = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((cb) => frames.push(cb));
    const caf = vi
      .spyOn(window, "cancelAnimationFrame")
      .mockImplementation(() => {});
    try {
      const frameClock = performance.now() - 1200;
      const { result } = renderHook(() => useCountUp(12.34, 50));
      const seen: number[] = [];
      for (let i = 0; i < 6 && frames.length > 0; i++) {
        const frame = frames.shift() as FrameRequestCallback;
        act(() => frame(frameClock + i * 16));
        seen.push(result.current);
      }
      expect(result.current).toBe(12.34);
      expect(Math.min(...seen)).toBeGreaterThanOrEqual(0);
    } finally {
      raf.mockRestore();
      caf.mockRestore();
    }
  });
});

describe("TgSheet", () => {
  it("renders nothing when closed", () => {
    render(
      <TgSheet open={false} onClose={vi.fn()} title="Task">
        <p>body</p>
      </TgSheet>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows title and children when open, closes on backdrop tap", () => {
    const onClose = vi.fn();
    render(
      <TgSheet open onClose={onClose} title="Fleet">
        <p>sheet body</p>
      </TgSheet>,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Fleet")).toBeInTheDocument();
    expect(screen.getByText("sheet body")).toBeInTheDocument();

    const [backdrop] = screen.getAllByRole("button", { name: /close/i });
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalled();
  });
});
