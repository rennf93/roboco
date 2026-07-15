import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Radix Tooltip renders content into a portal only when open; for a static
// render we assert on the trigger passthrough and the short-circuit, not the
// (hidden) portal content.

import { HelpTip } from "../help-tip";

describe("HelpTip", () => {
  it("wraps the child so the child still renders", () => {
    render(
      <HelpTip label="reload">
        <button type="button">Refresh</button>
      </HelpTip>,
    );
    expect(screen.getByText("Refresh")).toBeInTheDocument();
  });

  it("renders the child bare when label is falsy", () => {
    const { container } = render(
      <HelpTip label={null}>
        <button type="button">Refresh</button>
      </HelpTip>,
    );
    // No Tooltip wrapper: just the button.
    expect(container.querySelector("button")).not.toBeNull();
    expect(container.firstChild).toBe(container.querySelector("button"));
  });

  it("renders the child bare when label is an empty string", () => {
    const { container } = render(
      <HelpTip label="">
        <span>x</span>
      </HelpTip>,
    );
    expect(container.firstChild).toBe(container.querySelector("span"));
  });
});