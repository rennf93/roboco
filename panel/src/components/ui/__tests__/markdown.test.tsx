import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { Markdown } from "../markdown";

const checklist = ["- [ ] first", "- [x] second", "- [ ] third"].join("\n");

describe("Markdown task-list checkboxes", () => {
  it("toggles the line of the checkbox that was clicked", async () => {
    const user = userEvent.setup();
    const onCheckboxChange = vi.fn();
    render(
      <Markdown onCheckboxChange={onCheckboxChange}>{checklist}</Markdown>,
    );

    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);

    await user.click(boxes[2]);
    expect(onCheckboxChange).toHaveBeenLastCalledWith(
      ["- [ ] first", "- [x] second", "- [x] third"].join("\n"),
    );

    await user.click(boxes[1]);
    expect(onCheckboxChange).toHaveBeenLastCalledWith(
      ["- [ ] first", "- [ ] second", "- [ ] third"].join("\n"),
    );
  });

  it("keeps each checkbox's line after a re-render", async () => {
    const user = userEvent.setup();
    const onCheckboxChange = vi.fn();
    const { rerender } = render(
      <Markdown onCheckboxChange={onCheckboxChange}>{checklist}</Markdown>,
    );
    rerender(
      <Markdown onCheckboxChange={onCheckboxChange} disabled={false}>
        {checklist}
      </Markdown>,
    );

    await user.click(screen.getAllByRole("checkbox")[0]);
    expect(onCheckboxChange).toHaveBeenLastCalledWith(
      ["- [x] first", "- [x] second", "- [ ] third"].join("\n"),
    );
  });

  it("renders read-only checkboxes without a toggle handler", () => {
    render(<Markdown>{checklist}</Markdown>);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes[1]).toBeChecked();
    expect(boxes[0]).toBeDisabled();
  });
});
