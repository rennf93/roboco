# X Post Response Schemas

**Date:** 2026-08-12 **Task:** 2133f5ef **Files:** `roboco/api/schemas/x.py`, `roboco/api/utils/x.py`

## What

The CEO's X (Twitter) post queue and history view are served by two Pydantic response models: `XPostResponse` (held drafts awaiting decision, `GET /api/x/posts`) and `XPostHistoryResponse` (acted-on drafts — posted or rejected — the CEO's history view). Both live in `roboco/api/schemas/x.py` and are built by `to_response` / `to_history_response` in `roboco/api/utils/x.py`.

Each draft carries a `source` string (`x_post`, `x_reply`, `x_feature`, `x_editorial`, `x_campaign`, `x_barfly`) and zero or one source-specific reference fields. The API reads each ref from the task's `orchestration_markers` via the matching `markers.get_x_*_ref` helper and serializes it into a typed Pydantic model, or `None` when the marker is absent. The serialization pattern is identical for every source: `XRefModel(**ref) if ref else None`.

## Source-specific ref fields

All six ref fields are optional (`| None = None`). Exactly one is populated per draft, keyed by the draft's `source`.

| Field | Model | Source | Shape |
|-------|-------|--------|-------|
| `release_version` | `str \| None` | `x_post` | Semantic version string (not a model) |
| `mention` | `XMentionRefModel` | `x_reply` | `id`, `author_id`, `text` |
| `feature` | `XFeatureRefModel` | `x_feature` | `slug`, `title` |
| `campaign` | `XCampaignRefModel` | `x_campaign` | `campaign_name`, `stage_label`, `publish_after`, `sequence` |
| `editorial` | `XEditorialRefModel` | `x_editorial` | `angle`, `rationale` |
| `barfly` | `XBarflyRefModel` | `x_barfly` | `tweet_id`, `author_handle`, `text`, `rationale` |

## The `editorial` field (new)

`XEditorialRefModel` carries the editorial angle and rationale behind a Megaphone Board Program X post. It was added in this task to close a gap surfaced by PR gate finding F-cea77220: the API serialized `x_campaign_ref` (War Room) but not `x_editorial_ref` (Megaphone), so the panel could render the campaign-guidance line but not the editorial angle line for a held editorial draft.

The model parallels `XCampaignRefModel` exactly in structure and serialization:

```python
class XEditorialRefModel(BaseModel):
    """The editorial angle and rationale behind a Megaphone X post."""

    angle: str
    rationale: str
```

The marker dict stored by `markers.set_x_editorial_ref` has exactly `{angle, rationale}` keys (set in `roboco/services/x_engine.py` when `XEngine.materialize_editorial_post` originates a draft from the `propose_editorial_post` Board Program verb). `markers.get_x_editorial_ref` returns `dict[str, Any] | None` — the same shape as `get_x_campaign_ref` — so the serialization is a direct parallel: `editorial = markers.get_x_editorial_ref(task)` then `editorial=XEditorialRefModel(**editorial) if editorial else None` in both `to_response` and `to_history_response`.

When the `x_editorial_ref` marker is absent (any non-editorial source), `editorial` is `None`. The field is placed after `campaign` on both response models, matching the source-registration order in the engine.

## Panel rendering

The panel's X post queue (`x-post-queue.tsx`) renders a `sourceMeta`-driven label/icon per source and a source-specific context line. With the `editorial` field now serialized, the panel renders the angle-guidance line for editorial drafts the same way it already renders the campaign-guidance line for War Room drafts. The frontend implementation (PR #920, 2026-08-12) mirrors the `XCampaignRef`/`campaignGuidance` pattern exactly: `lib/api/x.ts` exports `XEditorialRef` (`{ angle, rationale }`) with `editorial?: XEditorialRef | null` on both `XPost` and `XPostHistoryEntry`; `x-post-queue.tsx` adds an `editorialGuidance(post)` function rendered as a HelpTip-wrapped context block; `x-post-detail.tsx` (TG approvals card) adds a `post.editorial`-guarded `text-xs text-muted-foreground` block. Both surfaces null-guard on `post.editorial`. See `docs/map/panel.md` for the full panel map.

## Tests

Four unit tests in `tests/unit/api/routes/test_x_project_fields.py` cover the serialization:

- `test_to_response_populates_editorial_when_marker_present` — asserts `editorial.angle` and `editorial.rationale` match the stubbed marker.
- `test_to_response_omits_editorial_when_marker_absent` — asserts `editorial is None` when `orchestration_markers` is `None`.
- `test_to_history_response_populates_editorial_when_marker_present` — same for the history response builder.
- `test_to_history_response_omits_editorial_when_marker_absent` — same for the history response builder.

The tests follow the existing stub pattern in that file (`SimpleNamespace` task stand-in, `sa_inspect` patch), extending `_stub_task` with a `markers` kwarg so an `x_editorial_ref` marker can be injected.

## Related

- `docs/rag/architecture/x-engine.md` — the X engine architecture, the six draft sources, their markers, the CEO endpoints, and the redraft-from-rejection flow
- `docs/backend/services/x-engine-redraft-markers.md` — how editorial/campaign ref markers are carried through X post redrafts after CEO rejection