import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TgTabBar } from "../tg-tab-bar";

describe("TgTabBar", () => {
  it("renders all 4 tabs and marks the active one with aria-current", () => {
    render(<TgTabBar active="inbox" onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: /approvals/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /board/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /chat/i })).toBeInTheDocument();

    const inbox = screen.getByRole("button", { name: /inbox/i });
    expect(inbox).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: /approvals/i }),
    ).not.toHaveAttribute("aria-current");
  });

  it("calls onChange with the tapped tab's id", () => {
    const onChange = vi.fn();
    render(<TgTabBar active="approvals" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /chat/i }));
    expect(onChange).toHaveBeenCalledWith("chat");

    fireEvent.click(screen.getByRole("button", { name: /board/i }));
    expect(onChange).toHaveBeenCalledWith("board");
  });
});
