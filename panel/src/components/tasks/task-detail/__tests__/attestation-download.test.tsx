import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// The download action is tested against the mocked typed client: the real
// useTaskAttestationDownload hook runs (fetch + save-file machinery) while
// tasksApi is stubbed at the client boundary.
const tasksApi = vi.hoisted(() => ({
  getAttestation: vi.fn(),
  getAttestationMarkdown: vi.fn(),
}));
vi.mock("@/lib/api/tasks", () => ({ tasksApi }));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// Radix DropdownMenu needs portal/pointer machinery jsdom lacks — stub it to
// render the items inline (mirrors agent-card.test.tsx's stub) so the item
// clicks exercise the component's real handlers.
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}));

// jsdom has no object-URL machinery. Capture every blob handed to the
// download helper so tests can assert the receipt's payload, and every
// transient anchor so tests can assert the suggested filename.
let lastBlob: Blob | null = null;
const anchors: HTMLAnchorElement[] = [];

beforeAll(() => {
  URL.createObjectURL = vi.fn((blob: Blob) => {
    lastBlob = blob;
    return "blob:mock-url";
  }) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();

  const realCreateElement = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation(
    (tag: string, options?: ElementCreationOptions) => {
      const el = realCreateElement(tag, options);
      if (tag === "a") anchors.push(el as HTMLAnchorElement);
      return el;
    },
  );
});

import { toast } from "sonner";
import { AttestationDownload } from "../attestation-download";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.AWAITING_PM_REVIEW,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    self_verified: true,
    qa_verified: null,
    ...overrides,
  } as unknown as Task;
}

function renderAttestationDownload(task: Task) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AttestationDownload task={task} />
    </QueryClientProvider>,
  );
}

describe("AttestationDownload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastBlob = null;
    anchors.length = 0;
  });

  it("renders a disabled action while the task is not yet attested", () => {
    renderAttestationDownload(
      buildTask({ self_verified: false, qa_verified: null }),
    );

    expect(
      screen.getByRole("button", { name: "Download attestation" }),
    ).toBeDisabled();
    // No menu items — there is nothing to download yet.
    expect(screen.queryByText(/Receipt \(/)).toBeNull();
    expect(tasksApi.getAttestationMarkdown).not.toHaveBeenCalled();
    expect(tasksApi.getAttestation).not.toHaveBeenCalled();
  });

  it("offers both receipt formats once the work is self-verified", () => {
    renderAttestationDownload(buildTask());

    expect(
      screen.getByRole("button", { name: "Download attestation" }),
    ).not.toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Receipt \(\.md\)/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Receipt \(\.json\)/i }),
    ).toBeInTheDocument();
  });

  it("downloads the Markdown receipt through the typed client", async () => {
    const user = userEvent.setup();
    tasksApi.getAttestationMarkdown.mockResolvedValue(
      "# Verification receipt\n\nAll criteria verified.\n",
    );
    renderAttestationDownload(buildTask());

    await user.click(screen.getByRole("button", { name: /Receipt \(\.md\)/i }));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Verification receipt (.md) downloaded",
      ),
    );
    expect(tasksApi.getAttestationMarkdown).toHaveBeenCalledWith("t1");
    expect(lastBlob).not.toBeNull();
    expect(await lastBlob!.text()).toContain("# Verification receipt");
    expect(anchors[0].download).toBe("attestation-t1.md");
  });

  it("downloads the full attestation as pretty-printed JSON", async () => {
    const user = userEvent.setup();
    tasksApi.getAttestation.mockResolvedValue({
      task_id: "t1",
      title: "Task",
      status: "completed",
      team: "backend",
      revision_count: 0,
      commits: [],
      work_sessions: [],
      acceptance_criteria: [{ text: "works", verified: true }],
      findings_by_round: [],
      ci: { state: "not_available", failing_checks: [] },
      conventions_findings: [],
      reviewer_chain: [],
      generated_at: "2026-09-01T00:00:00Z",
    });
    renderAttestationDownload(buildTask());

    await user.click(
      screen.getByRole("button", { name: /Receipt \(\.json\)/i }),
    );

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Verification receipt (.json) downloaded",
      ),
    );
    expect(tasksApi.getAttestation).toHaveBeenCalledWith("t1");
    expect(await lastBlob!.text()).toContain('"acceptance_criteria"');
    expect(anchors[0].download).toBe("attestation-t1.json");
  });

  it("toast-errors a failed download without crashing", async () => {
    const user = userEvent.setup();
    tasksApi.getAttestationMarkdown.mockRejectedValueOnce(
      new Error("Request failed with status code 404"),
    );
    renderAttestationDownload(buildTask());

    await user.click(screen.getByRole("button", { name: /Receipt \(\.md\)/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Failed to download receipt: Request failed with status code 404",
      ),
    );
    expect(lastBlob).toBeNull();
  });
});
