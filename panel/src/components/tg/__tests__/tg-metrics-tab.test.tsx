import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgMetricsTab } from "../tg-metrics-tab";

vi.mock("@/lib/telegram/demo", () => ({ isTgDemoMode: () => true }));
// Scorecard resolution needs the roster only outside demo mode (the demo
// scorecard fixture returns unconditionally) — an empty roster keeps this
// hook off the network without affecting anything the tests assert on.
vi.mock("@/hooks/use-agents", () => ({ useAgents: () => ({ data: [] }) }));

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TgMetricsTab />
    </QueryClientProvider>,
  );
}

describe("TgMetricsTab", () => {
  it("renders every hub section from the demo fixtures", async () => {
    renderTab();

    // Hero total — the demo agent/team/model/series slices all sum to $66.54.
    expect(await screen.findByText("$66.54")).toBeInTheDocument();
    expect(screen.getByText("By agent")).toBeInTheDocument();
    expect(screen.getByText("By team")).toBeInTheDocument();
    expect(screen.getByText("By model")).toBeInTheDocument();
    expect(screen.getByText("Delivery")).toBeInTheDocument();
    expect(screen.getByText("Efficiency")).toBeInTheDocument();

    // Top agent by cost (be-dev-1, $18.42) renders first with its real name.
    expect(await screen.findByText("Backend Dev 1")).toBeInTheDocument();
    // A team row's exact label (distinct from "Backend Dev 1" above).
    expect(await screen.findByText("Backend")).toBeInTheDocument();
  });

  it("switches the selected period on the segmented control", async () => {
    renderTab();
    await screen.findByText("$66.54");

    const oneWeek = screen.getByRole("button", { name: "1W" });
    const oneMonth = screen.getByRole("button", { name: "1M" });
    expect(oneWeek).toHaveAttribute("aria-pressed", "true");
    expect(oneMonth).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(oneMonth);

    expect(oneMonth).toHaveAttribute("aria-pressed", "true");
    expect(oneWeek).toHaveAttribute("aria-pressed", "false");
  });

  it("pushes the agent drilldown when a by-agent row is tapped", async () => {
    renderTab();
    await screen.findByText("$66.54");

    await userEvent.click(await screen.findByText("Backend Dev 1"));

    expect(
      await screen.findByRole("heading", { name: "Backend Dev 1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
  });

  it("shows the drilled-in agent's scorecard from the demo fixture", async () => {
    renderTab();
    await screen.findByText("$66.54");
    await userEvent.click(await screen.findByText("Backend Dev 1"));

    expect(await screen.findByText("Scorecard")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument(); // tasks_completed
  });

  it("returns to the hub from the drilldown's back button", async () => {
    renderTab();
    await screen.findByText("$66.54");
    await userEvent.click(await screen.findByText("Backend Dev 1"));
    await screen.findByRole("heading", { name: "Backend Dev 1" });

    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText("By agent")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Backend Dev 1" }),
    ).not.toBeInTheDocument();
  });
});
