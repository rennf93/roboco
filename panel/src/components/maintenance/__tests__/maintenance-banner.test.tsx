import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { MaintenancePauseStatus } from "@/lib/api/maintenance";

const { getStatus, resume } = vi.hoisted(() => ({
  getStatus: vi.fn<() => Promise<MaintenancePauseStatus[]>>(),
  resume: vi.fn(),
}));

vi.mock("@/lib/api/maintenance", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/maintenance")>(
    "@/lib/api/maintenance",
  );
  return {
    ...actual,
    maintenanceApi: { list: getStatus, pause: vi.fn(), resume },
  };
});

import { MaintenanceBanner } from "../maintenance-banner";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("MaintenanceBanner, persistent unmissable paused-state bar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when no scope is paused (the correct empty state)", async () => {
    getStatus.mockResolvedValue([]);
    render(withQueryClient(<MaintenanceBanner />));

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a distinct error strip (not a blank page) when the status fetch fails", async () => {
    getStatus.mockRejectedValue(new Error("network down"));
    render(withQueryClient(<MaintenanceBanner />));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/status unavailable/i);
    expect(alert).toHaveTextContent(/unknown/i);
  });

  it("DEFECT 3: flags a scope whose runtime gate reads degraded even though paused is false", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "dispatch",
        paused: false,
        paused_by: null,
        paused_at: null,
        reason: null,
        expires_at: null,
        read_degraded_since: "2026-08-12T09:00:00.000Z",
      },
    ]);
    render(withQueryClient(<MaintenanceBanner />));

    const alert = await screen.findByRole("alert", {
      name: /read degraded/i,
    });
    expect(alert).toHaveTextContent("Delivery dispatch");
    expect(alert).toHaveTextContent(/read degraded/i);
    // Distinct from a human pause: no "paused by" attribution, no Resume
    // button -- there is nothing here for the CEO to resume.
    expect(
      screen.queryByRole("button", { name: /resume/i }),
    ).not.toBeInTheDocument();
  });

  it("renders nothing for a scope that is neither paused nor read-degraded", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "dispatch",
        paused: false,
        paused_by: null,
        paused_at: null,
        reason: null,
        expires_at: null,
        read_degraded_since: null,
      },
    ]);
    render(withQueryClient(<MaintenanceBanner />));

    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows who paused, when, the reason, and a countdown to the server-set expiry", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "dispatch",
        paused: true,
        paused_by: "ceo",
        paused_at: "2026-08-12T10:00:00.000Z",
        reason: "NAS migration window",
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
      },
    ]);
    render(withQueryClient(<MaintenanceBanner />));

    const alert = await screen.findByRole("alert", {
      name: /maintenance pause active/i,
    });
    expect(alert).toHaveTextContent("Delivery dispatch");
    expect(alert).toHaveTextContent(/paused by ceo/i);
    expect(alert).toHaveTextContent(/NAS migration window/i);
    expect(alert).toHaveTextContent(/auto-resumes in/i);
  });

  it("shows a live countdown that ticks down toward the auto-expiry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const expiresAt = new Date(Date.now() + 90_000).toISOString();
    getStatus.mockResolvedValue([
      {
        scope: "board_programs",
        paused: true,
        paused_by: "ceo",
        paused_at: new Date().toISOString(),
        reason: null,
        expires_at: expiresAt,
      },
    ]);
    render(withQueryClient(<MaintenanceBanner />));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/auto-resumes in/i),
    );
    const firstReading = screen.getByRole("alert").textContent ?? "";

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    const secondReading = screen.getByRole("alert").textContent ?? "";
    expect(secondReading).not.toBe(firstReading);
    expect(secondReading).toMatch(/auto-resumes in/i);

    vi.useRealTimers();
  });

  it("refetches from the server instead of assuming resumed when the local countdown reaches zero", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const expiresAt = new Date(Date.now() + 2000).toISOString();
    getStatus.mockResolvedValue([
      {
        scope: "engines",
        paused: true,
        paused_by: "ceo",
        paused_at: new Date().toISOString(),
        reason: null,
        expires_at: expiresAt,
      },
    ]);
    render(withQueryClient(<MaintenanceBanner />));

    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(1));

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    // The countdown hitting zero triggers a real refetch, it never just
    // flips the row to "resumed" from the client's own clock.
    await waitFor(() => expect(getStatus.mock.calls.length).toBeGreaterThan(1));

    vi.useRealTimers();
  });

  it("resumes a scope in one click, calling resume with only that scope", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "engines",
        paused: true,
        paused_by: "ceo",
        paused_at: "2026-08-12T10:00:00.000Z",
        reason: null,
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
      },
    ]);
    resume.mockResolvedValue({
      scope: "engines",
      paused: false,
      paused_by: null,
      paused_at: null,
      reason: null,
      expires_at: null,
    });
    render(withQueryClient(<MaintenanceBanner />));

    const resumeButton = await screen.findByRole("button", {
      name: "Resume Originating engines",
    });
    resumeButton.click();

    await waitFor(() => expect(resume).toHaveBeenCalledWith("engines"));
  });
});
