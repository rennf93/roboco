import { describe, it, expect } from "vitest";
import {
  buildReviewerChain,
  groupFindingsByRound,
  parseAcVerificationStamps,
  PR_CI_VERDICT_UNAVAILABLE,
  RELEASE_MEMBER_TASK_IDS_UNAVAILABLE,
} from "../verification";
import type { TaskFinding } from "../tasks";

function finding(overrides: Partial<TaskFinding>): TaskFinding {
  return {
    id: "f-1",
    task_id: "task-1",
    origin: "qa",
    round: 1,
    author_slug: "fe-qa",
    file: null,
    line: null,
    severity: "major",
    criterion: null,
    expected: "expected",
    actual: "actual",
    fix: null,
    evidence: null,
    status: "open",
    addressed_by_commit: null,
    resolution_note: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

describe("parseAcVerificationStamps", () => {
  it("marks a criterion verified when its [AC] stamp line is present in qa_notes", () => {
    const qaNotes =
      "Looks good overall.\n\n" +
      "[AC] Hooks live in panel/src/hooks — verified: use-verification.ts:12\n" +
      "[AC] No JSX in hook files — verified: grepped, zero JSX found";

    const stamps = parseAcVerificationStamps(
      [
        "Hooks live in panel/src/hooks",
        "No JSX in hook files",
        "Unrelated criterion",
      ],
      qaNotes,
    );

    expect(stamps).toEqual([
      {
        criterion: "Hooks live in panel/src/hooks",
        verified: true,
        evidence: "use-verification.ts:12",
      },
      {
        criterion: "No JSX in hook files",
        verified: true,
        evidence: "grepped, zero JSX found",
      },
      { criterion: "Unrelated criterion", verified: false, evidence: null },
    ]);
  });

  it("marks every criterion unverified when qa_notes is null", () => {
    const stamps = parseAcVerificationStamps(["A", "B"], null);
    expect(stamps).toEqual([
      { criterion: "A", verified: false, evidence: null },
      { criterion: "B", verified: false, evidence: null },
    ]);
  });

  it("ignores non-[AC] lines (e.g. finding renderings) mixed into qa_notes", () => {
    const qaNotes =
      "[F-abc12345] file.ts:10 (major) — expected foo actual bar\n" +
      "[AC] A — verified: yep";
    const stamps = parseAcVerificationStamps(["A"], qaNotes);
    expect(stamps).toEqual([
      { criterion: "A", verified: true, evidence: "yep" },
    ]);
  });
});

describe("groupFindingsByRound", () => {
  it("groups consecutive same-round findings, preserving each finding's severity and status", () => {
    const findings: TaskFinding[] = [
      finding({ id: "f-2a", round: 2, origin: "pr_gate", severity: "minor" }),
      finding({
        id: "f-2b",
        round: 2,
        origin: "pr_gate",
        severity: "blocker",
        status: "waived",
      }),
      finding({ id: "f-1", round: 1, origin: "qa", status: "addressed" }),
    ];

    expect(groupFindingsByRound(findings)).toEqual([
      {
        round: 2,
        origin: "pr_gate",
        findings: [findings[0], findings[1]],
      },
      { round: 1, origin: "qa", findings: [findings[2]] },
    ]);
  });

  it("starts a new group when the same round number is not contiguous", () => {
    const findings: TaskFinding[] = [
      finding({ id: "f-1a", round: 1 }),
      finding({ id: "f-2a", round: 2 }),
      finding({ id: "f-1b", round: 1 }),
    ];

    expect(groupFindingsByRound(findings).map((g) => g.round)).toEqual([
      1, 2, 1,
    ]);
  });

  it("returns an empty list for a task never bounced", () => {
    expect(groupFindingsByRound([])).toEqual([]);
  });
});

describe("buildReviewerChain", () => {
  it("groups by round, keeps the first entry's author/origin per round, sorted ascending", () => {
    const findings: TaskFinding[] = [
      finding({
        id: "f-2",
        round: 2,
        origin: "pr_gate",
        author_slug: "fe-pr-reviewer",
      }),
      finding({ id: "f-1", round: 1, origin: "qa", author_slug: "fe-qa" }),
      finding({
        id: "f-1b",
        round: 1,
        origin: "qa",
        author_slug: "fe-qa-other",
      }),
    ];

    expect(buildReviewerChain(findings)).toEqual([
      { round: 1, origin: "qa", author_slug: "fe-qa", model: null },
      {
        round: 2,
        origin: "pr_gate",
        author_slug: "fe-pr-reviewer",
        model: null,
      },
    ]);
  });

  it("returns an empty chain for a task never bounced", () => {
    expect(buildReviewerChain([])).toEqual([]);
  });
});

describe("PR_CI_VERDICT_UNAVAILABLE", () => {
  it("always reports unavailable with an escalation reason (no-CI-repo case)", () => {
    expect(PR_CI_VERDICT_UNAVAILABLE.available).toBe(false);
    expect(PR_CI_VERDICT_UNAVAILABLE.reason.length).toBeGreaterThan(0);
  });
});

describe("RELEASE_MEMBER_TASK_IDS_UNAVAILABLE", () => {
  it("always reports unavailable with an escalation reason (no member-task endpoint)", () => {
    expect(RELEASE_MEMBER_TASK_IDS_UNAVAILABLE.available).toBe(false);
    expect(RELEASE_MEMBER_TASK_IDS_UNAVAILABLE.reason.length).toBeGreaterThan(
      0,
    );
  });
});
