import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReleaseProposal } from "@/lib/api/release";

// 2026-10-28: the release on the Overview card carried 74 member tasks and
// its verification rollup fired four requests per member (task, findings,
// task metrics, attestation): ~300 requests per render, repeated on every
// refetch, until the orchestrator's DB pool and the guard's Redis pool were
// exhausted. The rollup must read every member through the two bulk
// endpoints, never per member, whatever the member count.
const MEMBER_IDS = Array.from(
  { length: 12 },
  (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
);
const PROPOSAL_ID = "11111111-1111-4111-8111-111111111111";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api/client", () => ({
  default: { get, post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { ReleaseProposalCard } from "../release-proposal-card";
import { PageRefreshProvider } from "@/components/providers";

function proposal(): ReleaseProposal {
  return {
    task_id: PROPOSAL_ID,
    title: "Cut v0.33.0",
    status: "awaiting_ceo_approval",
    member_task_ids: MEMBER_IDS.map((task_id) => ({ task_id, pr_number: 7 })),
    report: {
      proposed_version: "0.33.0",
      bump_kind: "minor",
      change_summary: [],
      drafted_changelog: "",
      version_bump_plan: [],
      gaps: [],
      migration_notes: [],
      gate_state: "green",
    },
  };
}

function receipt(task_id: string) {
  return {
    task_id,
    title: `Member ${task_id.slice(-2)}`,
    acceptance_criteria: [],
    qa_notes: null,
    findings: [],
    conventions_findings: [],
    reviewer_chain: [],
  };
}

function respond(url: string): { data: unknown } {
  if (url === "/release/proposal") return { data: proposal() };
  if (url === "/release/proposal/verification")
    return {
      data: { proposal_task_id: PROPOSAL_ID, members: MEMBER_IDS.map(receipt) },
    };
  if (url === "/release/proposal/ci-verdicts")
    return {
      data: {
        proposal_task_id: PROPOSAL_ID,
        verdicts: MEMBER_IDS.map((task_id) => ({
          task_id,
          ci: { state: "success", head_sha: "abc1234", failing_checks: [] },
        })),
      },
    };
  return { data: {} };
}

function memberRequests(): string[] {
  return get.mock.calls
    .map(([url]) => String(url))
    .filter((url) => MEMBER_IDS.some((id) => url.includes(id)));
}

describe("ReleaseProposalCard verification rollup fan-out", () => {
  it("reads every member through two bulk requests, none per member", async () => {
    get.mockImplementation(async (url: string) => respond(url));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <PageRefreshProvider>
          <ReleaseProposalCard />
        </PageRefreshProvider>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getAllByText(/AC verified/)).toHaveLength(
        MEMBER_IDS.length,
      ),
    );
    const urls = get.mock.calls.map(([url]) => String(url));
    expect(memberRequests()).toEqual([]);
    expect(
      urls.filter((u) => u === "/release/proposal/verification"),
    ).toHaveLength(1);
    expect(
      urls.filter((u) => u === "/release/proposal/ci-verdicts"),
    ).toHaveLength(1);
  });
});
