# Organizational Structure

## Hierarchy

```
CEO (Renzo - Human)
    |
    +-- Board (4 agents)
    |    +-- Product Owner
    |    +-- Head of Marketing
    |    +-- Auditor (silent observer)
    |    +-- PR Reviewer (read-only safety check on PRs)
    |
    +-- Main PM
         |
         +-- Backend Cell
         +-- Frontend Cell
         +-- UX/UI Cell
```

## Agent Count

| Role | Count |
|------|-------|
| CEO | 1 (human — not counted in the 25) |
| Product Owner | 1 |
| Head of Marketing | 1 |
| Auditor | 1 |
| Main PR Reviewer | 1 (pr-reviewer-1; external/fork + internal PRs + the root→master gate) |
| Main PM | 1 |
| Cell PMs | 3 |
| Developers | 6 (2 per cell) |
| QAs | 3 (1 per cell) |
| Documenters | 3 (1 per cell) |
| Cell PR Reviewers | 3 (1 per cell; the in-path cell→root gate) |
| Prompter (Intake) | 1 (on-demand) |
| Secretary | 1 (on-demand) |
| **Total AI agents** | **25** |

## On-Demand Roles (Human-Facing)

Two of the 25 — the Prompter (Intake) and the Secretary — sit outside the standing delivery org above. They are **human-only** and **spawned on demand** as live chat sessions; they are counted among the 25, but unlike the standing org they have no lifecycle verbs and no outward agent comms:

| Role | Purpose |
|------|---------|
| Prompter (Intake) | Interviews the CEO and drafts a board-ready task |
| Secretary | The CEO's chief-of-staff; reads company state and runs gated CEO directives |

Neither has lifecycle verbs or outward agent comms. See [Prompter](../roles/prompter.md) and [Secretary](../roles/secretary.md).

## Cells

| Cell | PM | Developers | QA | Documenter | PR Reviewer |
|------|-----|------------|-----|------------|-------------|
| Backend | be-pm | be-dev-1, be-dev-2 | be-qa | be-doc | be-pr-reviewer |
| Frontend | fe-pm | fe-dev-1, fe-dev-2 | fe-qa | fe-doc | fe-pr-reviewer |
| UX/UI | ux-pm | ux-dev-1, ux-dev-2 | ux-qa | ux-doc | ux-pr-reviewer |

The cell PR reviewer runs the **in-path PR-review gate** on its cell's assembled cell→root PR (`claim_gate_review` → `pr_pass` / `pr_fail`); `pr-reviewer-1` runs the same gate on the root→master PR plus the inbound external/fork + internal PR review.

## Teams

| Team | Members |
|------|---------|
| executive | ceo |
| board | product-owner, head-marketing, auditor, pr-reviewer-1 |
| management | main-pm, be-pm, fe-pm, ux-pm |
| developers | all devs |
| qa | all QAs |
| documentation | all documenters |

## Escalation Chain

```
Developer/QA/Documenter → Cell PM → Main PM → Product Owner → CEO
```

## Communication

Each role can communicate with:

| Role | Can Communicate With |
|------|---------------------|
| CEO | Everyone |
| Board | CEO, other board, Main PM |
| Auditor | Everyone (silent read all) |
| PR Reviewer | Read-only; posts one change-request on the PR itself, no agent comms |
| Main PM | CEO, Board, Cell PMs |
| Cell PM | Main PM, cell members |
| Cell Members | Cell PM, other cell members |
