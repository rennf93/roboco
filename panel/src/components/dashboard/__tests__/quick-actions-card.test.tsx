import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useUIStore } from "@/store";
import { DEFAULT_QUICK_ACTION_IDS } from "../quick-actions-registry";
import { QuickActionsCard } from "../quick-actions-card";

function resetStore(quickActionIds: string[] = DEFAULT_QUICK_ACTION_IDS) {
  useUIStore.setState({ quickActionIds });
}

describe("QuickActionsCard", () => {
  beforeEach(() => {
    resetStore();
  });

  it("renders the default action set as links to their real routes on a fresh store", () => {
    render(<QuickActionsCard />);

    expect(screen.getByRole("link", { name: /New Task/i })).toHaveAttribute(
      "href",
      "/prompter",
    );
    expect(screen.getByRole("link", { name: /^Tasks$/i })).toHaveAttribute(
      "href",
      "/tasks",
    );
    expect(screen.getByRole("link", { name: /Settings/i })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("drops a stale id (removed from the registry) without crashing", () => {
    resetStore(["tasks", "this-action-no-longer-exists", "settings"]);

    render(<QuickActionsCard />);

    expect(screen.getByRole("link", { name: /^Tasks$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });

  it("renders nothing but the customize button when the stored list is entirely stale", () => {
    resetStore(["ghost-one", "ghost-two"]);

    render(<QuickActionsCard />);

    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(
      screen.getByRole("button", { name: "Customize Quick Actions" }),
    ).toBeInTheDocument();
  });

  describe("customize dialog", () => {
    async function openDialog(user: ReturnType<typeof userEvent.setup>) {
      render(<QuickActionsCard />);
      await user.click(
        screen.getByRole("button", { name: "Customize Quick Actions" }),
      );
      return screen.getByRole("dialog");
    }

    it("unchecking an action removes it from the card and persists to the store", async () => {
      const user = userEvent.setup();
      const dialog = await openDialog(user);

      await user.click(
        within(dialog).getByRole("checkbox", { name: "Show Tasks" }),
      );

      expect(useUIStore.getState().quickActionIds).not.toContain("tasks");
    });

    it("checking an action not in the default set adds it", async () => {
      const user = userEvent.setup();
      const dialog = await openDialog(user);

      // Auditor isn't in DEFAULT_QUICK_ACTION_IDS.
      await user.click(
        within(dialog).getByRole("checkbox", { name: "Show Auditor" }),
      );

      expect(useUIStore.getState().quickActionIds).toContain("auditor");
    });

    it("reorders with the move-later arrow", async () => {
      const user = userEvent.setup();
      const dialog = await openDialog(user);

      const before = useUIStore.getState().quickActionIds;
      expect(before[0]).toBe("new-task");

      await user.click(
        within(dialog).getByRole("button", { name: "Move New Task later" }),
      );

      const after = useUIStore.getState().quickActionIds;
      expect(after[1]).toBe("new-task");
      expect(after[0]).toBe(before[1]);
    });

    it("the move-earlier arrow is disabled for the first row", async () => {
      const user = userEvent.setup();
      const dialog = await openDialog(user);

      expect(
        within(dialog).getByRole("button", { name: "Move New Task earlier" }),
      ).toBeDisabled();
    });

    it("resets to defaults", async () => {
      const user = userEvent.setup();
      const dialog = await openDialog(user);

      await user.click(
        within(dialog).getByRole("checkbox", { name: "Show Tasks" }),
      );
      expect(useUIStore.getState().quickActionIds).not.toEqual(
        DEFAULT_QUICK_ACTION_IDS,
      );

      await user.click(
        within(dialog).getByRole("button", { name: "Reset to defaults" }),
      );

      expect(useUIStore.getState().quickActionIds).toEqual(
        DEFAULT_QUICK_ACTION_IDS,
      );
    });

    it("drops a stale id from the checklist instead of rendering it", async () => {
      const user = userEvent.setup();
      resetStore(["tasks", "ghost-stale"]);
      render(<QuickActionsCard />);
      await user.click(
        screen.getByRole("button", { name: "Customize Quick Actions" }),
      );
      const dialog = screen.getByRole("dialog");

      expect(within(dialog).queryByText("ghost-stale")).not.toBeInTheDocument();
      expect(
        within(dialog).getByRole("checkbox", { name: "Show Tasks" }),
      ).toBeInTheDocument();
    });
  });
});
