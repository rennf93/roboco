import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Production-triage bug: a manual spawn POSTed TWICE 2.5ms apart, both
// rejected "Agent already running" with no visible reason. Covers the fix:
// a synchronous re-entrancy guard against the double-fire, an
// already_running-aware toast, and the real backend refusal message
// reaching the CEO instead of a generic "Failed to spawn agent".

const { mutateAsync, toastSuccess, toastError, toastInfo } = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
}));

vi.mock("@/hooks/use-agents", () => ({
  useSpawnAgent: () => ({ mutateAsync, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: toastInfo },
}));

// client.ts registers axios interceptors that pull in the rate-limit store
// at import time; stub it so importing the real getErrorMessage is side-effect
// free (mirrors lib/__tests__/client.test.ts).
vi.mock("@/store/rate-limit-store", () => ({
  useRateLimitStore: { getState: vi.fn(() => ({ hitRateLimit: vi.fn() })) },
}));

import { SpawnAgentDialog } from "../spawn-agent-dialog";

function openDialog() {
  render(
    <SpawnAgentDialog
      agentId="fe-dev-2"
      agentName="fe-dev-2"
      trigger={<button type="button">Open Spawn</button>}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open Spawn" }));
}

function submitButton() {
  return screen.getByRole("button", { name: /Spawn Agent/i });
}

describe("SpawnAgentDialog", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("submits task id and initial prompt from the form", async () => {
    mutateAsync.mockResolvedValue({ already_running: false });
    openDialog();

    fireEvent.change(screen.getByLabelText(/Task ID/i), {
      target: { value: "task-123" },
    });
    fireEvent.change(screen.getByLabelText(/Initial Prompt/i), {
      target: { value: "go fix it" },
    });
    fireEvent.click(submitButton());

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      agentId: "fe-dev-2",
      request: { task_id: "task-123", initial_prompt: "go fix it" },
    });
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("shows a distinct toast when the spawn was skipped as already-running", async () => {
    mutateAsync.mockResolvedValue({ already_running: true });
    openDialog();
    fireEvent.click(submitButton());

    await waitFor(() => expect(toastInfo).toHaveBeenCalledTimes(1));
    expect(toastInfo.mock.calls[0][0]).toMatch(/already running/i);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("surfaces the backend's actual refusal reason, not a generic message", async () => {
    // Shape returned by axios on the 409 AgentReadinessError mapping.
    const axiosLikeError = {
      isAxiosError: true,
      message: "Request failed with status code 409",
      response: {
        status: 409,
        data: {
          detail:
            "spawn refused for fe-dev-2 (task=t1): state=awaiting_qa " +
            "requires role in {'qa'} but agent fe-dev-2 is 'developer'",
        },
      },
    };
    mutateAsync.mockRejectedValue(axiosLikeError);
    openDialog();
    fireEvent.click(submitButton());

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError.mock.calls[0][0]).toContain("state=awaiting_qa");
    expect(toastError.mock.calls[0][0]).not.toBe("Failed to spawn agent");
  });

  it("blocks a second submit fired before the first mutation settles", async () => {
    let resolveSpawn: (v: { already_running: boolean }) => void = () => {};
    mutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSpawn = resolve;
        }),
    );
    openDialog();

    // Two synchronous clicks, mirroring the 2.5ms-apart double-fire from the
    // production report — the second must never reach mutateAsync.
    fireEvent.click(submitButton());
    fireEvent.click(submitButton());

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    resolveSpawn({ already_running: false });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledTimes(1);
  });
});
