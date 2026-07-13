import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssigneeAvatar } from "../assignee-avatar";

// tooltip-aria-label-spec.md §1b: truncated content (two-letter initials)
// standing in for a full value needs a Tooltip disclosing that full value.

describe("AssigneeAvatar — full-name tooltip (tooltip-aria-label-spec §1b)", () => {
  it("shows the full agent display name in the tooltip once hovered", async () => {
    const user = userEvent.setup();
    render(<AssigneeAvatar agentId="fe-dev-1" />);

    // Radix mounts TooltipContent only once the trigger is hovered/focused.
    await user.hover(screen.getByText("FD1"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Frontend Dev 1",
    );
  });

  it("renders nothing for an unassigned task", () => {
    const { container } = render(<AssigneeAvatar agentId={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
