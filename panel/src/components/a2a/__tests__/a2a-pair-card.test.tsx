import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { A2APairCard, PairAvatar } from "../a2a-pair-card";

function buildPair(
  overrides: Partial<AdminPairSummary> = {},
): AdminPairSummary {
  return {
    agent_a: "be-dev-1",
    role_a: "developer",
    team_a: "backend",
    agent_b: "be-qa",
    role_b: "qa",
    team_b: "backend",
    group_key: "cell-backend",
    conversation_id: "conv-1",
    last_message_at: "2026-07-02T09:00:00Z",
    message_count: 5,
    ...overrides,
  };
}

describe("A2APairCard", () => {
  it("renders both display names, message count, and relative time", () => {
    render(<A2APairCard pair={buildPair()} pulsedAt={null} onOpen={vi.fn()} />);
    expect(screen.getByText(/Backend Dev 1/)).toBeInTheDocument();
    expect(screen.getByText(/Backend QA/)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/ago$/)).toBeInTheDocument();
  });

  it("fires onOpen when clicked", () => {
    const onOpen = vi.fn();
    render(<A2APairCard pair={buildPair()} pulsedAt={null} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId("pair-card"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("renders dimmed and shows 'No A2A yet' for a never-talked pair", () => {
    render(
      <A2APairCard
        pair={buildPair({ conversation_id: null, last_message_at: null })}
        pulsedAt={null}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText("No A2A yet")).toBeInTheDocument();
    expect(screen.getByTestId("pair-card")).toHaveClass("opacity-60");
    // No message-count badge for a pair with no history.
    expect(screen.queryByText("5")).not.toBeInTheDocument();
  });

  it("colors each avatar by team, not a per-agent hue", () => {
    render(<PairAvatar slug="fe-dev-1" />);
    expect(screen.getByText("FD1").parentElement).toHaveClass(
      "border-violet-500/40",
    );
  });

  it("shows the full agent display name in a hover tooltip (tooltip-aria-label-spec §1b)", async () => {
    const user = userEvent.setup();
    render(<PairAvatar slug="fe-dev-1" />);
    await user.hover(screen.getByText("FD1"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Frontend Dev 1",
    );
  });

  it("marks the card as selected via aria-pressed", () => {
    render(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={null}
        isSelected
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

describe("A2APairCard pulsing (frame -> matching card lights up, then fades)", () => {
  // Capture the rAF callback instead of letting it fire on the real event
  // loop, so the activation/fade transition is fully deterministic.
  let rafCallback: FrameRequestCallback | null = null;

  beforeEach(() => {
    rafCallback = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallback = cb;
      return 1;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts cold with no pulse", () => {
    render(<A2APairCard pair={buildPair()} pulsedAt={null} onOpen={vi.fn()} />);
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "false",
    );
  });

  it("goes hot the instant a matching pulsedAt is received", () => {
    const { rerender } = render(
      <A2APairCard pair={buildPair()} pulsedAt={null} onOpen={vi.fn()} />,
    );

    rerender(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000000000}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "true",
    );
  });

  it("fades back to cold on the next paint frame (CSS transition then does the decay)", () => {
    const { rerender } = render(
      <A2APairCard pair={buildPair()} pulsedAt={null} onOpen={vi.fn()} />,
    );
    rerender(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000000000}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "true",
    );

    act(() => {
      rafCallback?.(0);
    });

    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "false",
    );
  });

  it("does not re-trigger the pulse for an unchanged pulsedAt", () => {
    const { rerender } = render(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000000000}
        onOpen={vi.fn()}
      />,
    );
    act(() => {
      rafCallback?.(0);
    });
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "false",
    );

    // Same pulsedAt as before (e.g. an unrelated parent re-render) — must
    // stay cooled, not flash again.
    rerender(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000000000}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "false",
    );
  });

  it("re-triggers on a newer pulsedAt after cooling down", () => {
    const { rerender } = render(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000000000}
        onOpen={vi.fn()}
      />,
    );
    act(() => {
      rafCallback?.(0);
    });
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "false",
    );

    rerender(
      <A2APairCard
        pair={buildPair()}
        pulsedAt={1700000005000}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pair-card")).toHaveAttribute(
      "data-pulsing",
      "true",
    );
  });
});
