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

describe("TaskFilters — stalled toggle", () => {
  it("does not render the stalled toggle when onStalledChange is omitted", () => {
    render(<TaskFilters {...baseProps()} />);
    expect(screen.queryByText("Stalled")).not.toBeInTheDocument();
  });

  it("shows the populated stalled count and toggles the filter on click", async () => {
    const user = userEvent.setup();
    const onStalledChange = vi.fn();
    render(
      <TaskFilters
        {...baseProps()}
        stalledFilter={false}
        onStalledChange={onStalledChange}
        stalledCount={3}
      />,
    );

    const toggle = screen.getByRole("button", { name: /stalled.*3/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);
    expect(onStalledChange).toHaveBeenCalledWith(true);
  });

  it("shows an explicit empty count when nothing is stalled", () => {
    render(
      <TaskFilters
        {...baseProps()}
        stalledFilter={false}
        onStalledChange={vi.fn()}
        stalledCount={0}
      />,
    );
    expect(
      screen.getByRole("button", { name: /stalled.*0/i }),
    ).toBeInTheDocument();
  });

  it("renders a distinct error indicator when the stalled fetch fails, never a bare count", () => {
    render(
      <TaskFilters
        {...baseProps()}
        stalledFilter={false}
        onStalledChange={vi.fn()}
        stalledCount={0}
        stalledError
      />,
    );
    const toggle = screen.getByRole("button", { name: /stalled/i });
    // The error icon renders instead of the "(0)" count text.
    expect(toggle).not.toHaveTextContent("(0)");
    expect(toggle.querySelector("svg.lucide-circle-alert")).toBeTruthy();
  });

  it("renders an active-filter chip and clears it when the stalled filter is on", async () => {
    const user = userEvent.setup();
    const onStalledChange = vi.fn();
    render(
      <TaskFilters
        {...baseProps()}
        stalledFilter={true}
        onStalledChange={onStalledChange}
        stalledCount={2}
      />,
    );

    const chip = screen.getByText("Stalled", {
      selector: '[data-slot="badge"]',
    });
    expect(chip).toBeInTheDocument();

    await user.click(screen.getByLabelText("Remove Stalled filter"));
    expect(onStalledChange).toHaveBeenCalledWith(false);
  });
});
