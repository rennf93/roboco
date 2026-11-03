import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  AcVerificationStamp,
  PrCiVerdict,
  ReviewerChainEntry,
} from "@/lib/api/verification";
import {
  AcVerificationList,
  CiVerdictBadge,
  ReviewerChainList,
} from "../receipt";

describe("AcVerificationList", () => {
  it("renders a verified criterion with a check and its evidence line", () => {
    const data: AcVerificationStamp[] = [
      {
        criterion: "Criterion A",
        matched: true,
        verified: true,
        unresolved: false,
        evidence: "file.ts:12",
      },
    ];
    render(<AcVerificationList data={data} isLoading={false} />);
    expect(screen.getByText("Criterion A")).toBeInTheDocument();
    expect(screen.getByText("file.ts:12")).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /possibly verified by id/i }),
    ).not.toBeInTheDocument();
  });

  it("renders a confidently-unverified criterion without the unresolved marker", () => {
    const data: AcVerificationStamp[] = [
      {
        criterion: "Criterion B",
        matched: false,
        verified: false,
        unresolved: false,
        evidence: null,
      },
    ];
    render(<AcVerificationList data={data} isLoading={false} />);
    expect(screen.getByText("Criterion B")).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /possibly verified by id/i }),
    ).not.toBeInTheDocument();
  });

  it("renders an unresolved (not confidently unverified) marker for a criterion QA may have stamped by id", () => {
    const data: AcVerificationStamp[] = [
      {
        criterion: "Criterion C",
        matched: false,
        verified: false,
        unresolved: true,
        evidence: null,
      },
    ];
    render(<AcVerificationList data={data} isLoading={false} />);
    expect(screen.getByText("Criterion C")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /possibly verified by id/i }),
    ).toBeInTheDocument();
  });
});

describe("ReviewerChainList", () => {
  it("states no rounds are recorded yet in the empty state", () => {
    render(<ReviewerChainList data={[]} isLoading={false} />);
    expect(
      screen.getByText(/no review rounds recorded yet/i),
    ).toBeInTheDocument();
  });

  it("renders each round's role, agent, and real model — no more escalation-only null", () => {
    const data: ReviewerChainEntry[] = [
      {
        round: 1,
        role: "qa",
        agent_slug: "fe-qa",
        model: "claude-sonnet-5-5",
        started_at: "2026-09-01T00:00:00Z",
      },
    ];
    render(<ReviewerChainList data={data} isLoading={false} />);
    expect(screen.getByText("Round 1")).toBeInTheDocument();
    expect(screen.getByText("fe-qa")).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-5-5")).toBeInTheDocument();
  });
});

describe("CiVerdictBadge", () => {
  it.each([
    ["success", "CI passed"],
    ["failure", "CI failed"],
    ["pending", "CI running"],
    ["pending_not_scheduled", "CI not started"],
    ["no_ci_configured", "No CI"],
    ["error", "CI unreadable"],
  ] as const)("labels the %s state %j", (state, label) => {
    const data: PrCiVerdict = {
      available: true,
      state,
      headSha: "abc1234def",
      failingChecks: [],
    };
    render(<CiVerdictBadge data={data} isLoading={false} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("names the failing checks next to a failed verdict", () => {
    const data: PrCiVerdict = {
      available: true,
      state: "failure",
      headSha: "abc1234def",
      failingChecks: ["Python quality gate", "Panel (Next.js)"],
    };
    render(<CiVerdictBadge data={data} isLoading={false} />);
    expect(
      screen.getByText("Failing: Python quality gate, Panel (Next.js)"),
    ).toBeInTheDocument();
  });

  it("renders no verdict as unavailable", () => {
    render(
      <CiVerdictBadge
        data={{ available: false, reason: "No CI verdict yet." }}
        isLoading={false}
      />,
    );
    expect(screen.getByText("CI verdict unavailable")).toBeInTheDocument();
  });
});
