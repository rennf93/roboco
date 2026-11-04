# Golden-task eval harness vs FrontierCode 1.1: gap analysis

Status: analysis only. This document makes no parity claim. The RoboCo harness
and FrontierCode measure different things, and the harness has no published
scored results here. For scored runs, see
[frontiercode-scored-run-report.md](frontiercode-scored-run-report.md).

## Sources and method

**RoboCo side** is read directly from the code at the time of writing:

- `roboco/eval/fixtures.py` (the `BenchTaskSpec` fixtures)
- `roboco/eval/runner.py` (`EvalRunner`, `BenchJudge`, scoring dataclasses)
- `roboco/eval/__main__.py` (the `python -m roboco.eval run` CLI)

**FrontierCode side** is taken only from Cognition's own posts, fetched on
2026-11-04:

- [FrontierCode 1.1](https://cognition.com/blog/frontier-code-1.1) (2026-07-07), cited below as **[1.1]**
- [Introducing FrontierCode](https://cognition.com/blog/frontier-code) (2026-06-08), cited below as **[1.0]**
- [RuntimeWire coverage](https://runtimewire.com/article/cognition-adds-grok-4-6-to-devin-s-multi-model-coding-platform), the article named in the task brief. Its page lists the Cognition posts above as sources; the benchmark facts here come from the Cognition posts, not from the article body.

Anything not stated in those posts is marked **unknown**. I did not read the
FrontierCode task set, rubric files, or leaderboard; I did not verify
Cognition's claims independently.

## What each one is

The harness replays a fixed set of small, authored `BenchTaskSpec` fixtures
through the real RoboCo delivery lifecycle (developer, QA, documenter, cell PM)
in a disposable environment, and records deterministic metrics plus a
local-model judge score (`runner.py` module docstring, `EvalRunner.run_cohort`).
It measures how a (role, model) cohort behaves inside RoboCo's own pipeline.

FrontierCode, per [1.0], is a benchmark of "code mergeability": whether a
maintainer would merge a model's PR, graded with unit tests, rubrics and
verifiers on tasks built by open-source maintainers from their own repos.

These are different instruments. The comparison below is about methodology,
not about which scores are higher.

## Comparison by dimension

### 1. Task sourcing

| | RoboCo harness | FrontierCode 1.1 |
|---|---|---|
| Origin | Authored by RoboCo engineers as in-repo Python fixtures (`FIXTURES` tuple in `fixtures.py`). Each seeds a few synthetic files under `bench/<key>/`. | Real PRs in open-source codebases ([1.1], "FrontierCode tasks are sourced from real PRs in open-source codebases"). Per [1.0], hand-selected by maintainers from multi-PR chains and freeform requests, built from repos they maintain. |
| Authors | RoboCo's own team. | 20+ open-source developers; 36 flagship repositories ([1.0]). |
| Realism | Toy scale: single-file functions such as `paginate()`, `summarize()`, `greet()`, a README flag, plus four seeded-defect review fixtures. | Real codebases; effort per task stated as 40+ hours ([1.0]). |

Gap: the harness has no real-PR-derived tasks and no real third-party
codebases. Every fixture is a synthetic stub authored by the same team that
builds the system under test.

### 2. Reviewer-defined criteria

- **Harness:** each fixture carries `acceptance_criteria` (a short tuple of
  plain-language statements) and a free-text `expectations` note. These are
  written by the fixture author, not by an independent maintainer. The
  acceptance criteria also drive the in-lifecycle QA per-criterion check, which
  is a RoboCo gate, not a benchmark grader. The `expectations` note is only
  consumed by `BenchJudge`, a local-model grader (`_build_judge_prompt`).
- **FrontierCode:** each task is graded against reviewer-defined criteria; 1.1
  says there are 1000+ ([1.1]). Per [1.0], they are defined by the maintainers
  and every task is manually reviewed by a Cognition researcher, with an
  adversarial-testing and calibration QC pipeline.
- Number of criteria per task in FrontierCode: **unknown** (only the aggregate
  "1000+" is stated).

Gap: the harness has 2 to 3 author-written criteria per fixture, no
maintainer authorship, no independent review of the criteria, and no recorded
calibration or false-positive/false-negative audit.

### 3. Blocking criteria: does any criterion zero a run?

- **Harness: no.** A repo search of `roboco/eval/` finds no blocker or
  blocking concept. `FixtureResult.passed` is
  `final_status == "completed" and not stalled`. Individual acceptance
  criteria have no weight and no hard-stop flag. A run can reach `completed`
  through the real lifecycle without any single criterion being a gate in the
  eval's own scoring; the lifecycle's QA and review gates do enforce criteria,
  but the eval does not distinguish a "central" criterion from a minor one,
  and a score is never zeroed by a failed criterion.
- **FrontierCode: yes.** [1.1] describes blockers as "requirements so central
  to the task that failing one caps the score for that run, much like a change
  requested in a real code review". Elsewhere the same post and [1.0] state
  "Solutions that don't pass blocking criteria receive 0", and define a
  pass as clearing all blocker criteria. Cognition's own wording differs
  between "caps" and "receive 0"; the results-section wording says 0. 1.1
  demoted 75 overly strict blockers to non-blocker status after an audit
  ([1.1]).
- How many criteria per task are blockers: **unknown**.

Gap: this is the largest methodological difference. The harness has a
completion gate but no weighted rubric and no per-criterion blocker.

### 4. Contamination and retrieval controls

- **FrontierCode:** [1.1] documents the concern that solutions to real-PR
  tasks may exist on the public internet (later upstream versions, mirrors,
  package registries). Its response is two safeguards: a "fair internet use"
  prompt, and a programmatic verifier that flags references to source PRs,
  upstream patches or files, and likely solution-bearing mirrors, and zeroes
  those runs. It reports unfair-use rates under 1% for every model evaluated
  with the prompt in place. It considered and rejected blocklists (about 1,200
  domains, still circumvented) and allowlists. Internet access is not disabled.
- **Harness:** none of this exists in code. Two facts follow from the design:
  1. Fixtures are authored, synthetic, and not drawn from public upstream
     PRs, so the specific "find the upstream fix" shortcut FrontierCode
     targets does not apply in the same form.
  2. That is not the same as a control. The fixtures are checked into a
     public-or-shareable repository, so a model could have seen them in
     training data (unknown; not tested). `ponytail`-style or seeded-defect
     fixtures have no held-out variant. The runner performs no scan of agent
     network traffic or tool calls for solution retrieval, and the spawned
     agents are not given a fair-use prompt or verified against one. Whether
     real-spawn containers have unrestricted internet access in a bench run
     is **unknown**; I did not trace the container network configuration.
- Related isolation the harness does have: a throwaway database, a local git
  origin, a fake GitHub REST layer, and `settings.api_url` pointed at the
  disposable orchestrator so spawned MCP servers cannot reach production
  (`runner.py` docstring, `_bench_environment`). That protects the operator's
  production state. It is not a contamination control against solution
  lookup.

Gap: no retrieval detection, no fair-use policy for the agent, and no
held-out fixture set.

### 5. Scale

| | RoboCo harness | FrontierCode 1.1 |
|---|---|---|
| Task count | 10 fixtures total in `FIXTURES`: 6 developer-role "ship" fixtures plus 4 QA-role seeded-defect fixtures. | 150 tasks in the Extended set; Main is 100; Diamond (50) deprecated in 1.1 ([1.0], [1.1]). |
| Runnable today | Developer-role only. `run_cohort` raises for any other role and filters to `target_role == "developer"`, so the 4 seeded-defect fixtures are defined but not runnable by the CLI. | n/a |
| Repeats | One run per fixture per cohort invocation; no repeat or averaging logic in `run_cohort`. | Each model run 5 times at every available reasoning effort, averaged ([1.0]). |
| Languages | Python fixtures only. | Tripled the language count relative to SWE-Bench Pro ([1.0]); the list itself is **unknown**. |

### 6. Scoring

- **Harness:** per fixture, deterministic metrics (`final_status`, `stalled`,
  `revision_count`, cycle time, token buckets, estimated cost via the
  `agent_spawn_sessions` join) plus a 1-to-5 judge score from a local Ollama
  model, regex-parsed from free text and explicitly marked
  `non_deterministic` in the JSON. Cohort aggregate is a pass rate
  (completed and not stalled), cost, tokens, mean cycle time and mean judge
  score. The judge sees the final diff, notes, and the fixture's own
  `expectations`; it does not execute tests or run verifiers. Judge
  validation against human labels: none recorded.
- **FrontierCode:** score is a weighted aggregate of rubric items; failing
  blockers gives 0; a separate pass rate requires clearing all blockers
  ([1.0], [1.1]). Grading uses an ensemble of unit tests, rubrics and
  verifiers ([1.0]). Scoring weights: **unknown**.
- The harness measures process outcomes (did the lifecycle finish, at what
  cost, with how many bounces). FrontierCode measures output quality against
  maintainer standards. Neither number can stand in for the other.

## What the harness has that is not in this comparison

Stated for balance, not as an offset against the gaps above. The harness runs
the full RoboCo lifecycle (build, QA, docs, PM merge), records cost and rework
per fixture, and defines seeded-defect fixtures to test whether review gates
catch known flaws. Whether FrontierCode measures lifecycle behaviour beyond a
single solution is **unknown**; the posts reviewed describe grading a
solution against criteria.

## Prioritized gaps as follow-up work items

1. **Add per-criterion blocker semantics to the harness scorer.** Extend
   `BenchTaskSpec` with weighted rubric items and a `blocking` flag; make a
   failed blocker zero the fixture score and surface it in `CohortResult.as_dict()`.
   Replaces "completed means passed" as the only pass signal.
2. **Add a retrieval-detection verifier plus a fair-use prompt.** Scan the
   agent's tool-call and network trajectory for access to solution-bearing
   sources, zero flagged runs, and document the policy given to the agent.
   First determine what network access real-spawn bench containers have
   today (currently unknown).
3. **Make the seeded-defect fixtures runnable.** Add a QA-role cohort path to
   `EvalRunner.run_cohort` so the 4 existing `target_role="qa"` fixtures are
   scored instead of filtered out.
4. **Add a real-PR-derived fixture track.** Define a repeatable method to turn
   merged PRs from permissively licensed repos into `BenchTaskSpec`s (base
   commit, task prompt, criteria), and add a small first batch alongside the
   authored ones.
5. **Get criteria reviewed independently.** Have someone other than the
   fixture author write or review each fixture's criteria, and record the
   review. Add an audit pass for over-strict criteria, since FrontierCode 1.1
   needed one ([1.1]).
6. **Validate the judge.** Compare `BenchJudge` scores to human labels on a
   sample and record agreement, or replace judge-only scoring with
   executable checks where the fixture allows (most current fixtures have
   literal expected values that a test could assert).
7. **Add repeated trials.** Run each fixture N times per cohort and report
   the mean and spread, so single-run noise is not read as a difference
   between cohorts.
8. **Add a held-out fixture split.** Keep a portion of fixtures out of the
   public repository so training-data exposure of the checked-in fixtures
   cannot inflate scores.
9. **Expand scale and language coverage.** Grow beyond 6 runnable fixtures
   and Python-only stubs; target size is a decision for the owner, not
   derived from FrontierCode's counts.
