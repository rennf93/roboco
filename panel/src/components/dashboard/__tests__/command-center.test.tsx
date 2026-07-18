import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Shallow-render the composition: mock every hook + child component so this
// test only checks that the social summary card replaced the two full X/
// video queues — each child's own behavior is covered by its own test file.
vi.mock("@/hooks/use-dashboard", () => ({
  useCeoOverview: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useAuditorFlags: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useRecentActivity: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/use-tasks", () => ({
  useTasks: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/use-usage", () => ({
  useUsageTimeSeries: () => ({
    data: undefined,
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("../team-health-cards", () => ({
  TeamHealthCards: () => <div>TeamHealthCardsStub</div>,
}));
vi.mock("../key-metrics-panel", () => ({
  KeyMetricsPanel: () => <div>KeyMetricsPanelStub</div>,
}));
vi.mock("../auditor-alerts-panel", () => ({
  AuditorAlertsPanel: () => <div>AuditorAlertsPanelStub</div>,
}));
vi.mock("../active-blockers-panel", () => ({
  ActiveBlockersPanel: () => <div>ActiveBlockersPanelStub</div>,
}));
vi.mock("../recent-activity-feed", () => ({
  RecentActivityFeed: () => <div>RecentActivityFeedStub</div>,
}));
vi.mock("../quick-actions-card", () => ({
  QuickActionsCard: () => <div>QuickActionsCardStub</div>,
}));
vi.mock("../ceo-approval-queue", () => ({
  CeoApprovalQueue: () => <div>CeoApprovalQueueStub</div>,
}));
vi.mock("../pr-review-queue", () => ({
  PrReviewQueue: () => <div>PrReviewQueueStub</div>,
}));
vi.mock("@/hooks/use-page-refresh", () => ({
  usePageRefresh: () => ({
    register: vi.fn(),
    unregister: vi.fn(),
    refresh: vi.fn(),
    loading: false,
    disabled: false,
  }),
}));
vi.mock("../release-proposal-card", () => ({
  ReleaseProposalCard: () => <div>ReleaseProposalCardStub</div>,
}));
vi.mock("../playbook-review-queue", () => ({
  PlaybookReviewQueue: () => <div>PlaybookReviewQueueStub</div>,
}));
vi.mock("../social-summary-card", () => ({
  SocialSummaryCard: () => <div>SocialSummaryCardStub</div>,
}));
vi.mock("../roadmap-review-queue", () => ({
  RoadmapReviewQueue: () => <div>RoadmapReviewQueueStub</div>,
}));
vi.mock("../strategy-signals-panel", () => ({
  StrategySignalsPanel: () => <div>StrategySignalsPanelStub</div>,
}));
vi.mock("../usage-overview-panel", () => ({
  UsageOverviewPanel: () => <div>UsageOverviewPanelStub</div>,
}));
vi.mock("../scorecard-overview-panel", () => ({
  ScorecardOverviewPanel: () => <div>ScorecardOverviewPanelStub</div>,
}));
vi.mock("../cost-trend-chart", () => ({
  CostTrendChart: () => <div>CostTrendChartStub</div>,
}));

import { CommandCenter } from "../command-center";

describe("CommandCenter", () => {
  it("renders the social summary card instead of the full X/video queues", () => {
    render(<CommandCenter />);
    expect(screen.getByText("SocialSummaryCardStub")).toBeInTheDocument();
    expect(screen.queryByText("XPostQueueStub")).not.toBeInTheDocument();
    expect(screen.queryByText("VideoPostQueueStub")).not.toBeInTheDocument();
  });

  it("renders every section", () => {
    render(<CommandCenter />);
    for (const stub of [
      "QuickActionsCardStub",
      "KeyMetricsPanelStub",
      "AuditorAlertsPanelStub",
      "UsageOverviewPanelStub",
      "ScorecardOverviewPanelStub",
      "CostTrendChartStub",
      "TeamHealthCardsStub",
      "CeoApprovalQueueStub",
      "StrategySignalsPanelStub",
      "PrReviewQueueStub",
      "SocialSummaryCardStub",
      "ReleaseProposalCardStub",
      "PlaybookReviewQueueStub",
      "RoadmapReviewQueueStub",
      "ActiveBlockersPanelStub",
      "RecentActivityFeedStub",
    ]) {
      expect(screen.getByText(stub)).toBeInTheDocument();
    }
  });

  it("orders sections top to bottom: quick actions/key cards, team health, then the rest", () => {
    render(<CommandCenter />);
    const order = [
      "QuickActionsCardStub",
      "KeyMetricsPanelStub",
      "TeamHealthCardsStub",
      "CeoApprovalQueueStub",
      "PrReviewQueueStub",
      "ReleaseProposalCardStub",
      "PlaybookReviewQueueStub",
      "RoadmapReviewQueueStub",
      "ActiveBlockersPanelStub",
    ];
    for (let i = 1; i < order.length; i++) {
      const earlier = screen.getByText(order[i - 1]);
      const later = screen.getByText(order[i]);
      // earlier precedes later in the DOM.
      expect(
        earlier.compareDocumentPosition(later) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("pairs PR Reviews and Social side by side ahead of the release/playbook/roadmap cards", () => {
    render(<CommandCenter />);
    const prReview = screen.getByText("PrReviewQueueStub");
    const social = screen.getByText("SocialSummaryCardStub");
    const release = screen.getByText("ReleaseProposalCardStub");
    // Same grid row: shared immediate parent.
    expect(prReview.parentElement).toBe(social.parentElement);
    expect(
      prReview.compareDocumentPosition(release) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
