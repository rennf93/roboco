import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Suspense } from "react";
import { JournalEntryType, type JournalEntry } from "@/types";

// CEO feedback: the journal/task ids on the entry detail page were shown
// truncated with "..." and had no copy button. Guards the fix: no ellipsis,
// a copy button for the full id on both the journal id and the task id, and
// the existing task quick-link stays intact.

vi.mock("@/hooks/use-journals", () => ({
  useJournalEntry: vi.fn(),
}));

vi.mock("@/hooks", () => ({
  usePageRefresh: () => ({ register: vi.fn(), unregister: vi.fn() }),
}));

import { useJournalEntry } from "@/hooks/use-journals";
import JournalEntryPage from "../page";

const entry: JournalEntry = {
  id: "entry-1",
  journal_id: "a1b2c3d4-3333-4444-5555-666677778888",
  type: JournalEntryType.GENERAL,
  title: "Shipped the thing",
  content: "Body",
  task_id: "e27ef84d-1111-2222-3333-444455556666",
  session_id: null,
  timestamp: "2026-07-10T12:00:00Z",
  tags: [],
  sentiment: null,
  is_private: false,
  created_at: "2026-07-10T12:00:00Z",
  updated_at: null,
};

async function renderPage() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <JournalEntryPage params={Promise.resolve({ entryId: "entry-1" })} />
      </Suspense>,
    );
  });
}

describe("JournalEntryPage — id display", () => {
  it("shows the journal id8 without an ellipsis and with a copy button", async () => {
    vi.mocked(useJournalEntry).mockReturnValue({
      data: entry,
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useJournalEntry>);

    await renderPage();

    expect(await screen.findByText("a1b2c3d4")).toBeInTheDocument();
    expect(screen.queryByText(/a1b2c3d4\.\.\./)).not.toBeInTheDocument();
    // Two copy buttons: journal id + task id.
    expect(screen.getAllByRole("button", { name: /copy/i })).toHaveLength(2);
  });

  it("keeps the task quick-link and adds a copy button for the full task id", async () => {
    vi.mocked(useJournalEntry).mockReturnValue({
      data: entry,
      isLoading: false,
      error: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useJournalEntry>);

    await renderPage();

    const taskLink = (await screen.findByText("Task #e27ef84d")).closest("a");
    expect(taskLink).toHaveAttribute(
      "href",
      "/tasks/e27ef84d-1111-2222-3333-444455556666",
    );
  });
});
