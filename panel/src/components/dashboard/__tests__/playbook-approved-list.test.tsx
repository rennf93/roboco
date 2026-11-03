import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import type { ReactNode } from "react";
import type { Playbook } from "@/lib/api/playbooks";

const { listApproved, archive, listDrafts, toast } = vi.hoisted(() => ({
  listApproved: vi.fn(),
  archive: vi.fn(),
  listDrafts: vi.fn(async () => []),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  playbooksApi: { listApproved, archive, listDrafts },
}));
vi.mock("sonner", () => ({ toast }));

import { PlaybookReviewQueue } from "../playbook-review-queue";

const pb = (id: string, title: string): Playbook => ({
  id,
  title,
  slug: id,
  problem: `problem of ${title}`,
  procedure: "1. ...",
  tags: ["git"],
  team: "backend",
  scope: "cell",
  status: "approved",
});

function renderQueue(
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
  const ui: ReactNode = (
    <QueryClientProvider client={client}>
      <PlaybookReviewQueue />
    </QueryClientProvider>
  );
  return { client, ...render(ui) };
}

async function openApproved() {
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Approved" }));
  fireEvent.click(screen.getByRole("tab", { name: "Approved" }));
}

describe("PlaybookReviewQueue — Approved view", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listDrafts.mockResolvedValue([]);
  });

  it("lists approved playbooks with title, problem, tags and team", async () => {
    listApproved.mockResolvedValue([pb("a", "Rebase a branch")]);
    renderQueue();
    await openApproved();
    expect(await screen.findByText("Rebase a branch")).toBeInTheDocument();
    expect(screen.getByText(/problem of Rebase a branch/)).toBeInTheDocument();
    expect(screen.getByText("git")).toBeInTheDocument();
    expect(screen.getByText("backend")).toBeInTheDocument();
  });

  it("shows loading then empty state", async () => {
    listApproved.mockResolvedValue([]);
    renderQueue();
    await openApproved();
    expect(screen.getByTestId("approved-loading")).toBeInTheDocument();
    expect(
      await screen.findByText("No approved playbooks yet."),
    ).toBeInTheDocument();
  });

  it("shows an error state when the list fails", async () => {
    listApproved.mockRejectedValue(new Error("boom"));
    renderQueue();
    await openApproved();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load approved playbooks.",
    );
  });

  it("archives after confirmation, toasts and invalidates both keys", async () => {
    listApproved.mockResolvedValue([pb("a", "Rebase a branch")]);
    archive.mockResolvedValue({});
    const { client } = renderQueue();
    const spy = vi.spyOn(client, "invalidateQueries");
    await openApproved();
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    expect(archive).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await waitFor(() => expect(archive).toHaveBeenCalledWith("a"));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const keys = spy.mock.calls.map((c) => c[0]?.queryKey);
    expect(keys).toContainEqual(["playbooks", "drafts"]);
    expect(keys).toContainEqual(["playbooks", "approved"]);
  });

  it("treats a 409 as non-fatal and keeps rendering", async () => {
    listApproved.mockResolvedValue([pb("a", "Rebase a branch")]);
    const err = new AxiosError("conflict");
    err.response = { status: 409 } as AxiosError["response"];
    archive.mockRejectedValue(err);
    renderQueue();
    await openApproved();
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "Approved" })).toBeInTheDocument();
  });
});
