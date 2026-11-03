# RoboCo vs. Cognition's FrontierCode 1.1

Cognition's FrontierCode 1.1 benchmark uses 150 tasks drawn from real open-source pull requests, 1,000+ reviewer-defined grading criteria, and blocking criteria that zero out a run. It was revised in July to catch models retrieving solutions online. It drives press coverage and model rankings.

Source: [Runtime Wire — Cognition adds Grok 4.6 to Devin's multi-model coding platform](https://runtimewire.com/article/cognition-adds-grok-4-6-to-devin-s-multi-model-coding-platform)

**Response decision:** no score is published and no parity is claimed. RoboCo has a golden-task harness, but it has no completed scored run to cite. This note records what was done about the signal, checked against this codebase rather than assumed.

## Verified facts

1. **The harness exists.** `python -m roboco.eval run` replays golden tasks through the real build, review, document, merge lifecycle (`roboco/eval/__main__.py`, `roboco/eval/runner.py`). Real spawn is wired in v0.28.0 #701.
2. **It is small.** Twelve authored fixtures (`roboco/eval/fixtures.py`): six developer-role, the default set, and six QA/PM seeded-defect. FrontierCode 1.1 has 150 tasks from real pull requests. The two are not comparable.
3. **No scored run completed.** The attempt in this sandbox failed on missing Docker and a shell guard. Zero run ids, zero scores. Full account: [frontiercode-scored-run-report.md](../evals/frontiercode-scored-run-report.md).
4. **The methodology gaps are written down.** Task sourcing, reviewer-defined criteria, blocking criteria, and retrieval controls are compared in [frontiercode-1.1-gap-analysis.md](../evals/frontiercode-1.1-gap-analysis.md).
5. **What changed.** Two docs landed: the gap analysis and the scored-run report. No code changed. The report has an empty results table that stays empty until a run produces a receipt.

## Why

A benchmark number without a receipt is the thing this company exists to avoid. The lifecycle-level scoring is a real difference from patch-only benchmarks. It is only worth stating once a run backs it.

## Next

Run the harness on a host with Docker and built agent images. Record the cohort, fixture keys, and `--json-out` file. Then publish the figures, each beside its run id.
