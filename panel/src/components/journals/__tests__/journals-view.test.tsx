import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { PageRefreshProvider } from "@/components/providers";

// Sentinel flagged a react-hooks/exhaustive-deps suppression guarding the
// mount-only localStorage-restore effect. Fixed via a useRef mount guard
// instead of an empty deps array — this suite locks in that the restore
// fires exactly once and never re-fires to clobber a later, intentional
// filter clear (the hazard an honest-but-naive deps array would introduce).

const mockReplace = vi.fn();
// Stable per-render objects, same idiom as a2a-view.test.tsx — a fresh
// object/URLSearchParams on every render would make the effect's deps
// (searchParams, router) look "changed" even when the real URL hasn't
// moved, which would mask the very bug this test guards against.
const mockRouter = { replace: mockReplace, push: vi.fn() };
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => searchParams,
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgents: () => ({ data: [], isLoading: false, refetch: vi.fn() }),
}));

import { JournalsView } from "../journals-view";

const STORAGE_KEY = "roboco-journals-state";

function withPageRefresh(ui: ReactNode) {
  return <PageRefreshProvider>{ui}</PageRefreshProvider>;
}

describe("JournalsView — mount-only localStorage restore", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    searchParams = new URLSearchParams();
    localStorage.clear();
  });

  it("restores the saved agent filter into the URL on a fresh visit with no params", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ agent: "be-dev-1", q: null, type: null, task: null }),
    );

    await act(async () => {
      render(withPageRefresh(<JournalsView />));
    });

    expect(mockReplace).toHaveBeenCalledTimes(1);
    expect(mockReplace).toHaveBeenCalledWith("/agents?agent=be-dev-1");
  });

  it("does not restore when the URL already carries params", async () => {
    searchParams = new URLSearchParams("agent=be-qa");
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ agent: "be-dev-1", q: null, type: null, task: null }),
    );

    await act(async () => {
      render(withPageRefresh(<JournalsView />));
    });

    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("never re-fires the restore after mount, even once params are cleared back to empty", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ agent: "be-dev-1", q: null, type: null, task: null }),
    );

    let rerender!: (ui: ReactNode) => void;
    await act(async () => {
      const result = render(withPageRefresh(<JournalsView />));
      rerender = result.rerender;
    });
    expect(mockReplace).toHaveBeenCalledTimes(1);
    mockReplace.mockClear();

    // Simulate the URL round-tripping to carry params (post-restore) and
    // then a user intentionally clearing every filter — a fresh
    // URLSearchParams("") is referentially new, matching how the real
    // `next/navigation` value changes across an actual navigation, so this
    // is the exact scenario the naive "add router/searchParams to deps"
    // fix would break by re-restoring over the user's clear.
    searchParams = new URLSearchParams("agent=be-dev-1");
    await act(async () => {
      rerender(withPageRefresh(<JournalsView />));
    });
    searchParams = new URLSearchParams("");
    await act(async () => {
      rerender(withPageRefresh(<JournalsView />));
    });

    expect(mockReplace).not.toHaveBeenCalled();
  });
});
