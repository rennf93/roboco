import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  AcVerificationStamp,
  ReviewerChainEntry,
} from "@/lib/api/verification";
import { AcVerificationList, ReviewerChainList } from "../receipt";

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
  it("states the ledger-only scope in the empty state", () => {
    render(<ReviewerChainList data={[]} isLoading={false} />);
    expect(
      screen.getByText(
        /only rounds that recorded findings appear here.*passed clean has no entry/i,
      ),
    ).toBeInTheDocument();
  });

  it("states the ledger-only scope as a caption above the populated list", () => {
    const data: ReviewerChainEntry[] = [
      { round: 1, origin: "qa", author_slug: "fe-qa", model: null },
    ];
    render(<ReviewerChainList data={data} isLoading={false} />);
    expect(
      screen.getByText(
        /only rounds that recorded findings appear here.*passed clean has no entry/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Round 1")).toBeInTheDocument();
    expect(screen.getByText("fe-qa")).toBeInTheDocument();
  });
});
