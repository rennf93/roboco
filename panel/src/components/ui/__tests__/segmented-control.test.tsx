import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SegmentedControl } from "../segmented-control";

const WINDOWS = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
];

describe("SegmentedControl", () => {
  it("renders every option as a tab", () => {
    render(
      <SegmentedControl
        options={WINDOWS}
        value="24h"
        onValueChange={() => {}}
        aria-label="Time window"
      />,
    );
    for (const opt of WINDOWS) {
      expect(screen.getByRole("tab", { name: opt.label })).toBeInTheDocument();
    }
  });

  it("marks only the selected option active", () => {
    render(
      <SegmentedControl
        options={WINDOWS}
        value="7d"
        onValueChange={() => {}}
        aria-label="Time window"
      />,
    );
    expect(screen.getByRole("tab", { name: "7d" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "24h" })).toHaveAttribute(
      "data-state",
      "inactive",
    );
  });

  it("calls onValueChange with the clicked option's value", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SegmentedControl
        options={WINDOWS}
        value="24h"
        onValueChange={onChange}
        aria-label="Time window"
      />,
    );
    await user.click(screen.getByRole("tab", { name: "90d" }));
    expect(onChange).toHaveBeenCalledWith("90d");
  });
});