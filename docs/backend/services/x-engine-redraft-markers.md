# X Engine: Editorial + Campaign Sources Registered in All Redraft Paths

**Date:** 2026-08-12 **Task:** 90cddb87 **File:** `roboco/services/x_engine.py`

## Change

Rejected editorial (Megaphone) and campaign (War Room) X posts were losing their `x_editorial_ref` / `x_campaign_ref` markers when auto-redrafted after CEO rejection. The redraft task inherited the correct source string but lost the source-specific reference marker — so the panel wouldn't render the angle/campaign-guidance line for the redraft, and the local model generating the revision body wouldn't receive the editorial/campaign context.

The fix registers `X_EDITORIAL_SOURCE` and `X_CAMPAIGN_SOURCE` in all three dict-dispatched redraft tables:

1. `_REDRAFT_IDENTITY_EXTRACTORS` — `_redraft_identity_x_editorial` returns `(X_EDITORIAL_SOURCE, angle)`, `_redraft_identity_x_campaign` returns `(X_CAMPAIGN_SOURCE, campaign_name:sequence)`.
2. `_REDRAFT_CONTEXT_BUILDERS` — `_redraft_context_x_editorial` returns the angle + rationale string, `_redraft_context_x_campaign` returns the campaign_name/stage_label/sequence/publish_after string.
3. `_REDRAFT_MARKER_CARRIERS` — `_carry_x_editorial_marker` copies `x_editorial_ref`, `_carry_x_campaign_marker` copies `x_campaign_ref`.

The ponytail gap comment that documented this exact omission was removed.

## Tests

Two new unit tests in `tests/unit/services/test_x_engine.py`:

- `test_redraft_from_rejection_editorial_carries_editorial_ref` — verifies angle + rationale preserved.
- `test_redraft_from_rejection_campaign_carries_campaign_ref` — verifies campaign_name, stage_label, publish_after, sequence preserved.