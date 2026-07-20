import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TgTabBar } from "../tg-tab-bar";

describe("TgTabBar", () => {
  it("renders all 5 tabs and marks the active one with aria-current", () => {
    render(<TgTabBar active="metrics" onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: /today/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /approvals/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /board/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /chat/i })).toBeInTheDocument();

    const metrics = screen.getByRole("button", { name: /metrics/i });
    expect(metrics).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: /approvals/i }),
    ).not.toHaveAttribute("aria-current");
  });

  it("does not render Inbox as a tab (it lives behind the header bell)", () => {
    render(<TgTabBar active="today" onChange={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /inbox/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onChange with the tapped tab's id", () => {
    const onChange = vi.fn();
    render(<TgTabBar active="approvals" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /chat/i }));
    expect(onChange).toHaveBeenCalledWith("chat");

    fireEvent.click(screen.getByRole("button", { name: /metrics/i }));
    expect(onChange).toHaveBeenCalledWith("metrics");
  });
});
