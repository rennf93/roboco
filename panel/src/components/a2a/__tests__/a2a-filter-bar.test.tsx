import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { A2AFilterBar } from "../a2a-filter-bar";
import { EMPTY_A2A_FILTERS, type A2AFilters } from "../a2a-filter-utils";

function renderBar(
  overrides: Partial<Omit<Parameters<typeof A2AFilterBar>[0], "filters">> & {
    filters?: A2AFilters;
  } = {},
) {
  const onFiltersChange = vi.fn();
  const { filters, ...rest } = overrides;
  render(
    <A2AFilterBar
      filters={filters ?? EMPTY_A2A_FILTERS}
      onFiltersChange={onFiltersChange}
      agentOptions={["be-dev-1", "be-qa"]}
      view="list"
      {...rest}
    />,
  );
  return { onFiltersChange };
}

describe("A2AFilterBar", () => {
  it("renders a collapsed trigger with no active-count badge by default", () => {
    renderBar();
    expect(
      screen.getByRole("button", { name: /^Filters$/ }),
    ).toBeInTheDocument();
  });

  it("shows the active-count badge on the trigger when filters are set", () => {
    renderBar({ filters: { ...EMPTY_A2A_FILTERS, agents: ["be-dev-1"] } });
    expect(
      screen.getByRole("button", { name: "Filters · 1" }),
    ).toBeInTheDocument();
  });

  it("opens the popover and renders the Agent checkbox list", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    expect(screen.getByText("Backend Dev 1")).toBeInTheDocument();
    expect(screen.getByText("Backend QA")).toBeInTheDocument();
  });

  it("fires onFiltersChange when an Agent checkbox is toggled", async () => {
    const user = userEvent.setup();
    const { onFiltersChange } = renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.click(screen.getByRole("checkbox", { name: "Backend Dev 1" }));
    expect(onFiltersChange).toHaveBeenCalledWith({
      ...EMPTY_A2A_FILTERS,
      agents: ["be-dev-1"],
    });
  });

  it("renders the Task id-fragment input and the No linked task toggle", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    expect(screen.getByLabelText("Task id fragment")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "No linked task" }),
    ).toBeInTheDocument();
  });

  it("fires onFiltersChange when the task id fragment is typed", async () => {
    const user = userEvent.setup();
    const { onFiltersChange } = renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.type(screen.getByLabelText("Task id fragment"), "a");
    expect(onFiltersChange).toHaveBeenCalledWith({
      ...EMPTY_A2A_FILTERS,
      taskIdFragment: "a",
    });
  });

  it("renders Status toggle buttons and two date-range inputs", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    expect(screen.getByRole("button", { name: "Active" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archived" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("From date")).toBeInTheDocument();
    expect(screen.getByLabelText("To date")).toBeInTheDocument();
  });

  it("fires onFiltersChange when a status button is toggled", async () => {
    const user = userEvent.setup();
    const { onFiltersChange } = renderBar();
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    await user.click(screen.getByRole("button", { name: "Active" }));
    expect(onFiltersChange).toHaveBeenCalledWith({
      ...EMPTY_A2A_FILTERS,
      statuses: ["active"],
    });
  });

  it("shows an inline note that Task/Status/Date apply to the List view only when view=switchboard", async () => {
    const user = userEvent.setup();
    renderBar({ view: "switchboard" });
    await user.click(screen.getByRole("button", { name: /^Filters$/ }));
    expect(
      screen.getByText(
        "Task, Status, and Date filters apply to the Conversation List view.",
      ),
    ).toBeInTheDocument();
  });

  it("renders one chip per active filter value plus a Clear all action", () => {
    renderBar({
      filters: {
        agents: ["be-dev-1"],
        taskIdFragment: "",
        noLinkedTask: false,
        statuses: ["active"],
        dateFrom: "2026-07-01",
        dateTo: "",
      },
    });
    expect(screen.getByText("Backend Dev 1")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("From 2026-07-01")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Clear all" }),
    ).toBeInTheDocument();
  });

  it("removes only the matching filter when a chip's remove button is clicked", async () => {
    const user = userEvent.setup();
    const { onFiltersChange } = renderBar({
      filters: {
        ...EMPTY_A2A_FILTERS,
        agents: ["be-dev-1"],
        statuses: ["active"],
      },
    });
    await user.click(
      screen.getByRole("button", { name: "Remove Active filter" }),
    );
    expect(onFiltersChange).toHaveBeenCalledWith({
      ...EMPTY_A2A_FILTERS,
      agents: ["be-dev-1"],
      statuses: [],
    });
  });

  it("resets every dimension when Clear all is clicked", async () => {
    const user = userEvent.setup();
    const { onFiltersChange } = renderBar({
      filters: {
        agents: ["be-dev-1"],
        taskIdFragment: "abc",
        noLinkedTask: true,
        statuses: ["active"],
        dateFrom: "2026-07-01",
        dateTo: "2026-07-05",
      },
    });
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onFiltersChange).toHaveBeenCalledWith(EMPTY_A2A_FILTERS);
  });

  it("renders no chip row or Clear all when no filters are active", () => {
    renderBar();
    expect(
      screen.queryByRole("button", { name: "Clear all" }),
    ).not.toBeInTheDocument();
  });
});
