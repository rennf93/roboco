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

  it("leaves task markers inside code untouched", async () => {
    const user = userEvent.setup();
    const onCheckboxChange = vi.fn();
    const content = [
      "Write `- [ ] item` for a task:",
      "",
      "```md",
      "- [ ] example",
      "```",
      "",
      "- [ ] real",
    ].join("\n");
    render(<Markdown onCheckboxChange={onCheckboxChange}>{content}</Markdown>);

    await user.click(screen.getByRole("checkbox"));
    expect(onCheckboxChange).toHaveBeenLastCalledWith(
      content.replace("- [ ] real", "- [x] real"),
    );
  });

  it.each([
    ["star and plus bullets", "* [ ] a\n+ [ ] b", "* [ ] a\n+ [x] b"],
    ["ordered items", "1. [ ] a\n2) [ ] b", "1. [ ] a\n2) [x] b"],
    ["wide bullet spacing", "- [ ] a\n-   [X] b", "- [ ] a\n-   [ ] b"],
    ["a blockquote", "> - [ ] a\n> - [ ] b", "> - [ ] a\n> - [x] b"],
    ["a nested list", "- [ ] a\n  - [ ] b", "- [ ] a\n  - [x] b"],
    ["CRLF line endings", "- [ ] a\r\n- [ ] b", "- [ ] a\r\n- [x] b"],
  ])("toggles the right line in %s", async (_case, content, expected) => {
    const user = userEvent.setup();
    const onCheckboxChange = vi.fn();
    render(<Markdown onCheckboxChange={onCheckboxChange}>{content}</Markdown>);

    await user.click(screen.getAllByRole("checkbox")[1]);
    expect(onCheckboxChange).toHaveBeenLastCalledWith(expected);
  });

  it("renders read-only checkboxes without a toggle handler", () => {
    render(<Markdown>{checklist}</Markdown>);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes[1]).toBeChecked();
    expect(boxes[0]).toBeDisabled();
  });
});
