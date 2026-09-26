"""Tests for the laya fine-tune corpus exporter: one labeled decision_log
row becomes one corpus example in the upstream notebook's shape; anything
unlabeled, unknown, unusable, or malformed is skipped loudly, never
guessed."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
import roboco.config as cfg
from roboco.services.decisions import export
from roboco.services.decisions.export import build_example, export_corpus

_UNSET = object()


def _row(
    *,
    pilot: str = "self_heal",
    outcome: Any = _UNSET,
    state: Any = _UNSET,
    questions: Any = _UNSET,
    row_id: str = "row-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        pilot=pilot,
        outcome="cleared_after_gate" if outcome is _UNSET else outcome,
        state=state
        if state is not _UNSET
        else {
            "repo": "roboco",
            "error_excerpt": "ConnectionError: read timed out",
            "attempt_number": 1,
        },
        questions=questions
        if questions is not _UNSET
        else {
            "gate": {
                "type": "noul",
                "instructions": (
                    "This failure is transient (infra/flake/network) rather "
                    "than a defect in the recent commits."
                ),
            }
        },
    )


def test_example_happy_path_noul() -> None:
    example = build_example(_row())
    assert example is not None
    assert example["id"] == "row-1"
    assert example["workflow"] == "self_heal"
    # The notebook json.loads()es all three payload fields.
    assert json.loads(example["state"])["repo"] == "roboco"
    assert json.loads(example["questions"])["gate"]["type"] == "noul"
    gold = json.loads(example["gold"])
    assert gold["gate"]["probabilities"] == {"true": 1.0, "false": 0.0}


def test_example_without_inputs_is_skipped() -> None:
    assert build_example(_row(state=None)) is None
    assert build_example(_row(questions=None)) is None


def test_example_without_outcome_is_skipped() -> None:
    assert build_example(_row(outcome=None)) is None


def test_example_with_unknown_outcome_is_skipped() -> None:
    assert build_example(_row(outcome="some_future_slug")) is None


def test_example_with_unusable_outcome_is_skipped() -> None:
    example = build_example(_row(outcome="superseded_by_fix_task"))
    assert example is None


def test_example_gold_labels_only_shared_question_keys() -> None:
    questions = {
        "gate": {"type": "noul", "instructions": "Transient?"},
        "weight": {"type": "score", "criteria": ["a", "b", "c"]},
    }
    example = build_example(_row(questions=questions))
    assert example is not None
    kept = json.loads(example["questions"])
    gold = json.loads(example["gold"])
    # Only the labeled intersection is exported, on both sides.
    assert set(kept) == {"gate"}
    assert set(gold) == {"gate"}


def test_example_with_zero_overlap_is_skipped() -> None:
    questions = {"weight": {"type": "score", "criteria": ["a", "b", "c"]}}
    assert build_example(_row(questions=questions)) is None


def test_example_with_malformed_gold_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        export,
        "gold_for",
        lambda pilot, outcome: {"gate": {"true": 0.5}},
    )
    example = build_example(_row())
    assert example is None  # probabilities must sum to 1


def test_example_choice_gold_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        export,
        "gold_for",
        lambda pilot, outcome: {"gate": {"park_standard": 1.0}},
    )
    questions = {
        "gate": {
            "type": "choice",
            "instructions": "Which handling applies?",
            "criteria": {"park_standard": "wait", "escalate": "tell a PM"},
        }
    }
    example = build_example(_row(questions=questions))
    assert example is not None
    gold = json.loads(example["gold"])
    assert gold["gate"]["probabilities"] == {"park_standard": 1.0}


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return self._rows


class _FakeExportSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_FakeExportSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeExportFactory:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __call__(self) -> _FakeExportSession:
        return _FakeExportSession(self._rows)


@pytest.mark.asyncio
async def test_export_corpus_writes_jsonl_and_skips_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import roboco.db.base as db_base

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    good = _row(row_id="row-1")
    skipped = _row(row_id="row-2", outcome="unknown_slug")
    rows = [good, skipped]

    def _factory() -> _FakeExportSession:
        return _FakeExportSession(rows)

    monkeypatch.setattr(db_base, "get_session_factory", lambda: _factory)
    out = tmp_path / "corpus.jsonl"
    written = await export_corpus(str(out), pilot=None, limit=100)
    assert written == 1
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    example = json.loads(lines[0])
    assert example["id"] == "row-1"
    assert example["workflow"] == "self_heal"


@pytest.mark.asyncio
async def test_export_failure_leaves_no_partial_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A failed export never leaves a truncated corpus file behind."""

    class _BoomSession:
        async def __aenter__(self) -> "_BoomSession":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, stmt: Any) -> Any:
            raise RuntimeError("db down")

    import roboco.db.base as db_base

    def _boom_factory() -> _BoomSession:
        return _BoomSession()

    monkeypatch.setattr(db_base, "get_session_factory", lambda: _boom_factory)
    out = tmp_path / "corpus.jsonl"
    with pytest.raises(RuntimeError, match="db down"):
        await export_corpus(str(out), pilot=None, limit=100)
    assert not out.exists()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".corpus-")]
    assert leftovers == []


def test_example_from_question_outcomes_fates() -> None:
    """Batched pilots grade per question through the fate map; waived
    findings drop out of the gold entirely."""
    row = _row(
        pilot="findings_mapping",
        state={"diff": "+fix", "files_changed": ["a.py"]},
        questions={
            "finding_0": {
                "type": "noul",
                "instructions": "The diff plausibly addresses finding 0.",
            },
            "finding_1": {
                "type": "noul",
                "instructions": "The diff plausibly addresses finding 1.",
            },
        },
    )
    row.question_outcomes = {"finding_0": "resolved", "finding_1": "waived"}
    example = build_example(row)
    assert example is not None
    gold = json.loads(example["gold"])
    # Only the fate-labeled, usable keys survive.
    assert set(gold) == {"finding_0"}
    assert gold["finding_0"]["probabilities"] == {"true": 1.0, "false": 0.0}


def test_state_key_is_content_addressed() -> None:
    from roboco.services.decisions.pilots import state_key

    a = state_key({"bump_kind": "minor", "summary": "x"})
    assert a == state_key({"summary": "x", "bump_kind": "minor"})
    assert a != state_key({"bump_kind": "patch", "summary": "x"})
