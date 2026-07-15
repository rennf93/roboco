import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatusChart } from "../task-status-chart";

describe("TaskStatusChart", () => {
  it("renders the card title", () => {
    render(
      <TaskStatusChart
        slices={[
          { name: "Completed", value: 5 },
          { name: "Pending", value: 0 },
        ]}
      />,
    );
    expect(
      screen.getByText("Task Status Distribution"),
    ).toBeInTheDocument();
  });

  it("shows an empty state when every slice is zero", () => {
    render(
      <TaskStatusChart
        slices={[
          { name: "Pending", value: 0 },
          { name: "Completed", value: 0 },
        ]}
      />,
    );
    expect(screen.getByText("No tasks")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(
      <TaskStatusChart
        slices={[{ name: "Pending", value: 0 }]}
        isLoading
      />,
    );
    expect(screen.queryByText("No tasks")).not.toBeInTheDocument();
  });
});