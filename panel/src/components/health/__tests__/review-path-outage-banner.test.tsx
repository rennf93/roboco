import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewPathOutageBanner } from "../review-path-outage-banner";
import type { ReviewPathOutageStatus } from "@/lib/api/system-health";

// The backend health surface ships in parallel — the contract is mocked
// here, per the task brief.
const useReviewPathOutage = vi.fn<
  () => ReviewPathOutageStatus | null | undefined
>();
vi.mock("@/hooks/use-review-path-outage", () => ({
  useReviewPathOutage: () => useReviewPathOutage(),
}));

function activeOutage(): ReviewPathOutageStatus {
  return {
    active: true,
    outage_type: "review_path_tools",
    window_start: "2026-09-05T09:00:00Z",
    recovered: false,
  };
}

describe("ReviewPathOutageBanner", () => {
  beforeEach(() => {
    useReviewPathOutage.mockReset();
  });

  it("renders the outage banner while active", () => {
    useReviewPathOutage.mockReturnValue(activeOutage());
    render(<ReviewPathOutageBanner />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("Review-path tools outage");
    expect(banner).toHaveTextContent("since");
  });

  it("renders nothing when there is no outage", () => {
    useReviewPathOutage.mockReturnValue({
      active: false,
      outage_type: "review_path_tools",
      window_start: null,
      recovered: true,
    });
    const { container } = render(<ReviewPathOutageBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("stays quiet while the backend surface has not landed (null)", () => {
    useReviewPathOutage.mockReturnValue(null);
    const { container } = render(<ReviewPathOutageBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("stays quiet while the first poll is in flight (undefined)", () => {
    useReviewPathOutage.mockReturnValue(undefined);
    const { container } = render(<ReviewPathOutageBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("disappears when recovery arrives", () => {
    useReviewPathOutage.mockReturnValue(activeOutage());
    const { rerender, container } = render(<ReviewPathOutageBanner />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    useReviewPathOutage.mockReturnValue({
      active: false,
      outage_type: "review_path_tools",
      window_start: "2026-09-05T09:00:00Z",
      recovered: true,
    });
    rerender(<ReviewPathOutageBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is read-only: it offers no action buttons", () => {
    useReviewPathOutage.mockReturnValue(activeOutage());
    render(<ReviewPathOutageBanner />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
