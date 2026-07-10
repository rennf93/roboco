import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { A2AFilterBar } from "../a2a-filter-bar";

describe("A2AFilterBar", () => {
  it("renders the search input and both status toggle buttons", () => {
    render(
      <A2AFilterBar
        status="all"
        onStatusChange={vi.fn()}
        search=""
        onSearchChange={vi.fn()}
      />,
    );
    expect(
      screen.getByPlaceholderText("Search agent or topic..."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Active" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
  });

  it("marks the current status button as pressed", () => {
    render(
      <A2AFilterBar
        status="active"
        onStatusChange={vi.fn()}
        search=""
        onSearchChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Active" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("fires onStatusChange when a status button is clicked", () => {
    const onStatusChange = vi.fn();
    render(
      <A2AFilterBar
        status="all"
        onStatusChange={onStatusChange}
        search=""
        onSearchChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Active" }));
    expect(onStatusChange).toHaveBeenCalledWith("active");
  });

  it("fires onSearchChange as the user types", () => {
    const onSearchChange = vi.fn();
    render(
      <A2AFilterBar
        status="all"
        onStatusChange={vi.fn()}
        search=""
        onSearchChange={onSearchChange}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Search agent or topic..."), {
      target: { value: "be-qa" },
    });
    expect(onSearchChange).toHaveBeenCalledWith("be-qa");
  });
});
