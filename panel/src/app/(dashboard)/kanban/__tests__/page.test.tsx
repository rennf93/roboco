import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

// /kanban and /kanban?view=X used to render the full kanban board; Stream3-A
// moved that board into the Tasks page's Kanban tab, so this route now only
// redirects there — this test locks in the redirect target for both the
// bare route and the view-preserving query-param case.
const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
let currentSearch = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

import KanbanPage from "../page";

describe("KanbanPage redirect (Stream3-B)", () => {
  beforeEach(() => {
    replace.mockReset();
    currentSearch = "";
  });

  it("redirects /kanban to /tasks?tab=kanban", async () => {
    render(<KanbanPage />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/tasks?tab=kanban"),
    );
  });

  it("redirects /kanban?view=qa to /tasks?tab=kanban&view=qa, preserving the view", async () => {
    currentSearch = "view=qa";
    render(<KanbanPage />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/tasks?tab=kanban&view=qa"),
    );
  });
});
