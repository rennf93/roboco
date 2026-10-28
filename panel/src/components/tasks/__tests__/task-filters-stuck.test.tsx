import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskStatus, Team } from "@/types";
import { TaskFilters } from "../task-filters";

function baseProps() {
  return {
    searchQuery: "",
    onSearchChange: vi.fn(),
    statusFilter: [] as TaskStatus[],
    onStatusChange: vi.fn(),
    teamFilter: [] as Team[],
    onTeamChange: vi.fn(),
  };
}

describe("TaskFilters — stuck toggle", () => {
  it("does not render the stuck toggle when onStuckChange is omitted", () => {
    render(<TaskFilters {...baseProps()} />);
    expect(
      screen.queryByRole("button", { name: /stuck/i }),
    ).not.toBeInTheDocument();
  });

  it("toggles the filter on click", async () => {
    const user = userEvent.setup();
    const onStuckChange = vi.fn();
    render(<TaskFilters {...baseProps()} onStuckChange={onStuckChange} />);

    const toggle = screen.getByRole("button", { name: /stuck/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(onStuckChange).toHaveBeenCalledWith(true);
  });

  it("shows a removable active badge and clears via Clear all", async () => {
    const user = userEvent.setup();
    const onStuckChange = vi.fn();
    render(
      <TaskFilters
        {...baseProps()}
        stuckFilter
        onStuckChange={onStuckChange}
      />,
    );

    expect(screen.getByRole("button", { name: /stuck/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByLabelText("Remove Stuck filter"));
    expect(onStuckChange).toHaveBeenCalledWith(false);

    onStuckChange.mockClear();
    await user.click(screen.getByRole("button", { name: /clear all/i }));
    expect(onStuckChange).toHaveBeenCalledWith(false);
  });
});
