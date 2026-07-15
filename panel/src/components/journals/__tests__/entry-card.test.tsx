import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EntryCard } from "../entry-card";
import { JournalEntryType, type JournalEntry } from "@/types";

// CEO feedback: journal entry ids were shown truncated with no way to copy
// the full id and no quick-link to the related task. Guards against
// regressing either fix, and against re-nesting the task link/copy button
// inside the card's own entry-detail <Link> (invalid nested anchors).

const baseEntry: JournalEntry = {
  id: "entry-11112222-3333-4444-5555-666677778888",
  journal_id: "journal-1",
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

describe("EntryCard — task id display", () => {
  it("shows the id8 prefix without an ellipsis", () => {
    render(<EntryCard entry={baseEntry} />);
    expect(screen.getByText("Task #e27ef84d")).toBeInTheDocument();
    expect(screen.queryByText(/e27ef84d\.\.\./)).not.toBeInTheDocument();
  });

  it("links the task badge to the task detail page", () => {
    render(<EntryCard entry={baseEntry} />);
    const taskLink = screen.getByText("Task #e27ef84d").closest("a");
    expect(taskLink).toHaveAttribute(
      "href",
      "/tasks/e27ef84d-1111-2222-3333-444455556666",
    );
  });

  it("renders a copy button for the full task id, separate from the task link", () => {
    render(<EntryCard entry={baseEntry} />);
    const copyButton = screen.getByRole("button", { name: /copy/i });
    expect(copyButton.closest("a")).toBeNull();
  });

  it("links the card body to the entry detail page", () => {
    render(<EntryCard entry={baseEntry} />);
    const entryLink = screen.getByText("Shipped the thing").closest("a");
    expect(entryLink).toHaveAttribute(
      "href",
      "/journals/entry-11112222-3333-4444-5555-666677778888",
    );
  });

  it("shows the full task id in a hover tooltip on the truncated badge", async () => {
    const user = userEvent.setup();
    render(<EntryCard entry={baseEntry} />);
    await user.hover(screen.getByText("Task #e27ef84d"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "e27ef84d-1111-2222-3333-444455556666",
    );
  });

  it("omits the task row entirely when there is no related task", () => {
    render(<EntryCard entry={{ ...baseEntry, task_id: null }} />);
    expect(screen.queryByText(/^Task #/)).not.toBeInTheDocument();
  });
});
