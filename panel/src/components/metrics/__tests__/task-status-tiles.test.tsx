import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatusTiles } from "../task-status-tiles";

describe("TaskStatusTiles", () => {
  it("renders a tile per status with its label and value", () => {
    render(
      <TaskStatusTiles
        tiles={[
          { label: "Pending", value: 3, icon: <span>icon</span> },
          { label: "Completed", value: 12, icon: <span>icon</span> },
        ]}
      />,
    );
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders one tile for every entry passed", () => {
    render(
      <TaskStatusTiles
        tiles={[
          { label: "A", value: 1, icon: <span>icon</span> },
          { label: "B", value: 2, icon: <span>icon</span> },
          { label: "C", value: 3, icon: <span>icon</span> },
          { label: "D", value: 4, icon: <span>icon</span> },
          { label: "E", value: 5, icon: <span>icon</span> },
        ]}
      />,
    );
    ["A", "B", "C", "D", "E"].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
  });
});
