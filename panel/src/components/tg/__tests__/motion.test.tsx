import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
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
    // Generous timeout: rAF frames starve under parallel test workers.
    await waitFor(() => expect(result.current).toBe(12.34), { timeout: 4000 });
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
