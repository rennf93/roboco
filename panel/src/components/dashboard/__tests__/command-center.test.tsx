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
vi.mock("../quick-actions-bar", () => ({
  QuickActionsBar: () => <div>QuickActionsBarStub</div>,
}));
vi.mock("../ceo-approval-queue", () => ({
  CeoApprovalQueue: () => <div>CeoApprovalQueueStub</div>,
}));
vi.mock("../pr-review-queue", () => ({
  PrReviewQueue: () => <div>PrReviewQueueStub</div>,
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

import { CommandCenter } from "../command-center";

describe("CommandCenter", () => {
  it("renders the social summary card instead of the full X/video queues", () => {
    render(<CommandCenter />);
    expect(screen.getByText("SocialSummaryCardStub")).toBeInTheDocument();
    expect(screen.queryByText("XPostQueueStub")).not.toBeInTheDocument();
    expect(screen.queryByText("VideoPostQueueStub")).not.toBeInTheDocument();
  });
});
