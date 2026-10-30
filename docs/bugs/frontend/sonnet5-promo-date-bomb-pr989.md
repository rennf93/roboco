# Sonnet-5 promo date-bomb — PR #989 red gate (stale diagnosis, zero-net fix)

## Description and reproduction

PR #989's assembled-PR Python quality gate went red because CI ran past a hard-coded promo cutoff embedded in `tests/unit/billing/test_pricing.py`. The task (`44593e20`) that was opened to repair it described a class named `TestSonnet5PromoTier` with 7 tests asserting the `claude-sonnet-5` promo rates unconditionally, failing once `roboco/billing/pricing.py`'s `_SONNET5_PROMO_END` cutoff (then 2026-08-31) passed. The prescribed fix was to monkeypatch that module constant to a far-future date for the promo assertions and add a complementary list-rate test class for the post-promo branch.

## Root cause analysis

By the time this task's dev picked up the work, the diagnosis was **stale**: a sibling task (`e324375d`, PR #990) had already landed on the same branch lineage and replaced the offending class outright. The branch-head `test_pricing.py` no longer contains any class named `TestSonnet5PromoTier` — the real sonnet-5 tier class is `TestSonnet5Tier` (line 216), which asserts every case dynamically against `p._sonnet5_prices()` instead of hard-coded rate literals, so it is date-safe on either side of the promo boundary with no monkeypatch needed. Post-promo list-rate coverage for the other branch of `_sonnet5_prices()` also already existed twice, via a frozen-clock `monkeypatch.setattr(p, "date", ...)` pattern: `test_sonnet5_list_rate_after_promo_ends` and `test_sonnet5_reverts_to_list_rate_after_2026_09_13`. Separately, production `roboco/billing/pricing.py`'s promo window was extended to `_SONNET5_PROMO_END = date(2026, 9, 13)` on the same lineage.

Round 1 of this task, working from the stale task description, added a third test class (`TestSonnet5ListRateAfterPromo`) using a different constant-pin mechanism — duplicate coverage that also broke the file's established frozen-date convention. QA caught this on review (findings `0a5f7db8`/`d025b356`): no `TestSonnet5PromoTier` class exists, and the post-promo branch was already covered twice.

## Solution implemented

Round 2 removed the redundant `TestSonnet5ListRateAfterPromo` class, restoring `test_pricing.py` to the state the sibling task had already left it in. `roboco/billing/pricing.py` was never touched by this task. The net diff this task contributes to the branch is therefore zero production or test change — the PR exists solely to hand the assembled PR a fresh head commit so its CI gate re-runs clean against the already-fixed test suite.

## Prevention measures

A task's diagnosis embedded in its description reflects the branch state *at the time the task was written*, not necessarily at claim time on a fast-moving assembled PR with multiple concurrent sibling fixes targeting the same file. Before implementing a prescribed fix, check whether the named class/symbol still exists at the current branch head (`grep`/read the file directly) rather than trusting the description's line numbers and class names verbatim — this is what caught the redundancy here, one round late.
