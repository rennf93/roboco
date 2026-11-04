# Change Note: Response to Thoughtworks Agent/works (June 2026)

On June 16, 2026, Thoughtworks launched Agent/works, a control plane and governed runtime for enterprise agents. This note records what we changed in response, and why.

## Why

Governance is now a funded category. Agent/works frames it as agent sprawl, risk and AI spend.

RoboCo already ships mechanisms for much of that. We under-marketed them:

- the conventions validator
- the CEO merge gate
- role separation
- budget caps

The launch release also cites an AppSec Santa figure: 25% of AI-generated code samples had critical vulnerabilities. A buyer who reads it can assume RoboCo output is unsafe too.

## What changed

Two pages were added under `docs/positioning`. No code changed. No product behavior changed.

### [thoughtworks-agent-works-2026-06.md](thoughtworks-agent-works-2026-06.md)

Maps each Agent/works claim to the RoboCo mechanism that answers it, with the file that implements it.

| Agent/works claim | RoboCo mechanism |
|---|---|
| Agent sprawl | One gateway, fixed verbs per role |
| Risk | Independent review chain, CEO sign-off |
| AI spend | Opt-in per-project and per-task budgets |
| Scoped permissions | Per-role verb manifests, bash guard |
| Pre-execution compliance | Conventions validator at submit and PR gates |

It also lists what RoboCo does not claim. It does not claim time-limited, per-grant permissions. It does not claim pre-execution compliance proofs. It does not claim to govern agents it did not spawn. It states that budget caps and the conventions validator are both off by default.

### [ai-code-risk-gates-2026-06.md](ai-code-risk-gates-2026-06.md)

Quotes the Sonar 42% and AppSec Santa 25% figures from the release. States that both are third-party figures and neither was measured on RoboCo output.

Lists nine verified facts about the gates between agent output and master:

- QA is never the author, and a QA pass needs per-criterion evidence.
- An independent PR-reviewer gate checks the assembled PR.
- `pr_pass` refuses on its own toolchain, conventions and CI checks.
- Only the CEO completes a task awaiting CEO approval.
- The conventions validator and the lint-suppression rule apply only where the standard is armed.
- `make quality` is the full merge gate and runs bandit. `make gate` does not.

## What the pages do not say

Neither page claims a vulnerability rate for RoboCo output. Neither claims the gates guarantee the absence of vulnerabilities. Neither claims a match to Agent/works feature for feature. Each page ends with its own does-not-claim list.

## Decision

No product change is warranted. The gap is positioning, not capability.
