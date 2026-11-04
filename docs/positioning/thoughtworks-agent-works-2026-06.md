# RoboCo vs. Thoughtworks Agent/works: Governance Claims Mapped to Repo Receipts

On June 16, 2026, Thoughtworks launched Agent/works, a control plane and governed runtime for enterprise agents. It is pitched at agent sprawl, risk and AI spend, and describes capability-based, scope-bound, time-limited permissions and pre-execution compliance proofs.

Source: [Thoughtworks Launches Agent/works to Govern and Run Enterprise AI Agents Across Any Cloud](https://www.thoughtworks.com/about-us/news/2026/thoughtworks-launches-agent-works)

URL check: the `launches` URL is the live release. The variant `.../thoughtworks-launched-agent-works` does not serve it. Cite the `launches` URL. The sibling note [ai-code-risk-gates-2026-06.md](ai-code-risk-gates-2026-06.md) records the same check.

For the two third-party figures in that release (Sonar 42% AI-assisted code, AppSec Santa 25% critical vulnerabilities), see [ai-code-risk-gates-2026-06.md](ai-code-risk-gates-2026-06.md). This note does not repeat them.

**Response decision:** no product change is warranted. Governance is now a funded category, and RoboCo already ships mechanisms for most of what Agent/works describes. They are under-marketed, not missing. This note maps each claim to the mechanism that answers it, cites the file, and says where RoboCo does not match. Every receipt below was read in this checkout.

## Claim to mechanism

| Agent/works claim | RoboCo mechanism | Receipt |
|---|---|---|
| Agent sprawl | One gateway, fixed verbs per role | `roboco/foundation/policy/lifecycle.py` (`intents_for_role`), `roboco/services/gateway/role_config.py` |
| Risk | Independent review chain, CEO sign-off | `lifecycle.py` (`qa_pass`, `pr_pass`, `ceo_approve`), `roboco/api/deps.py` (`require_ceo_role`) |
| AI spend | Opt-in per-project and per-task budgets | `roboco/config.py` (`task_budgets_enabled`), `roboco/models/project.py` (`monthly_budget_usd`), `roboco/models/task.py` (`budget_usd`) |
| Scoped permissions | Per-role verb manifests, bash guard | `role_config.py`, `docker/scripts/hooks/bash-guard-hook.sh` |
| Pre-execution compliance | Conventions validator at submit and PR gates | `.roboco/conventions.yml`, `Settings.conventions_enabled` |

## Verified facts

1. **Sprawl is bounded by a fixed set of verbs per role.** Agents do not call the API or tools directly. Each role gets a manifest built from `intents_for_role(role)` in `roboco/foundation/policy/lifecycle.py`; `roboco/services/gateway/role_config.py` assembles the per-role flow lists (`_DEV_FLOW`, `_QA_FLOW`, `_DOC_FLOW`, `_CELL_PM_FLOW`, `_MAIN_PM_FLOW`) from that one function. A developer cannot call a QA verb because the verb is not in the developer manifest, and the lifecycle spec rejects it again server-side.
2. **Review is role-separated, and the reviewer is never the author.** `qa_pass` and `qa_fail` are restricted to QA roles and `pr_pass` and `pr_fail` to the PR reviewer and DevOps roles, all with `self_review_block=True` (`ACTIONS` in `roboco/foundation/policy/lifecycle.py`). The sibling note cites the exact transitions.
3. **Only the CEO completes a task awaiting CEO approval.** `ceo_approve` is restricted to `frozenset({Role.CEO})` in `lifecycle.py`. `require_ceo_role()` in `roboco/api/deps.py` is the shared CEO check that other CEO-only routes call. The company charter lists "CEO approves every merge to master" as a standing constraint.
4. **Spend caps exist and are off by default.** `task_budgets_enabled` defaults to `False` (`Settings.task_budgets_enabled`, `roboco/config.py`). When on, a claim is refused once a project's `monthly_budget_usd` is reached, and a budget sweep blocks an active task whose own `budget_usd` is breached and notifies the CEO. When off, neither cap is consulted regardless of the field values. The sweep lives in `roboco/runtime/engines/sweeps.py`.
5. **The permission boundary on an agent's shell is a guard, not a policy language.** `docker/scripts/hooks/bash-guard-hook.sh` denies raw git network and branch operations, direct calls to the orchestrator API, and reads of credential files. It is a fixed deny list. See the does-not-claim list.
6. **The conventions validator runs before a change leaves the author, and it is opt-in.** `.roboco/conventions.yml` declares which kinds of definition may live in which module, and `i_am_done` and `pr_pass` refuse on block-level findings. `Settings.conventions_enabled` defaults to `False`, so every hook is inert until an operator turns it on. The sibling note gives the full detail on both gates.

## Does not claim

- It does not claim RoboCo has capability-based, scope-bound, time-limited permissions in the sense Agent/works describes. RoboCo scopes by role and verb, with a fixed bash deny list. There is no per-grant expiry and no time-limited token issued for an individual action.
- It does not claim pre-execution compliance proofs. The conventions validator is a deterministic placement and hygiene check at submit and PR time. It does not produce a signed proof, and it covers architecture rules, not regulatory compliance.
- It does not claim a control plane for agents RoboCo did not spawn. RoboCo governs its own agent roster, not arbitrary third-party agents across any cloud.
- It does not claim spend caps are active by default. `task_budgets_enabled` is `False` until an operator sets it.
- It does not claim the conventions validator is on by default. It is off unless `conventions_enabled` is set.
- It does not claim any enterprise attestation, audit certification or customer outcome. No such measurement is made or cited here.
- It does not claim to match Agent/works feature for feature. Its internals were not inspected beyond the launch release.
