"""Export labeled decision_log rows into the laya fine-tune corpus format.

One decision_log row that carries BOTH the question inputs (``state`` /
``questions``, recorded exactly as sent on the wire) and a ground-truth
``outcome`` becomes one training example for the laya checkpoint:

    {"id": ..., "workflow": "self_heal", "state": "<json>",
     "questions": "<json>", "gold": "<json>"}

- ``workflow`` is the pilot slug (the corpus's domain column).
- ``state`` / ``questions`` are the row's JSON payloads, serialized as
  strings exactly the way the ``LocalLLaMA/typed-decisions`` corpus and
  the upstream fine-tune notebook read them (``json.loads(row[field])``).
- ``gold`` maps question key -> ``{"probabilities": {...}}`` from the
  outcome registry in ``outcomes.py``. Only question keys labeled by the
  registry are emitted, and a row whose outcome is unknown for its pilot
  or marked unusable (``superseded_by_fix_task``) is skipped, never
  guessed. Malformed gold is skipped loudly: a corrupt training row is
  worse than a missing one.

Offline tooling (reads decision_log, writes JSONL): run from the
orchestrator environment::

    python -m roboco.services.decisions.export --out corpus.jsonl \
        [--pilot self_heal] [--limit 5000]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from roboco.db.tables import DecisionLogTable
from roboco.services.decisions.outcomes import (
    example_gold,
    gold_for,
    validate_gold_shape,
)

logger = structlog.get_logger(__name__)

_QTYPES = ("noul", "choice", "score")


def _checked_probabilities(
    qtype: str, probability_map: dict[str, float]
) -> dict[str, float] | None:
    """One gold map coerced to (str-key, float) shape, or ``None`` when
    the question type is unknown or the map fails its shape check."""
    if qtype not in _QTYPES or not validate_gold_shape(qtype, probability_map):
        return None
    return {str(k): float(v) for k, v in probability_map.items()}


def _labeled_gold(
    row_id: str, pilot: str, outcome: str, questions: dict[str, Any]
) -> tuple[dict[str, dict[str, float]], dict[str, Any]] | None:
    """Resolve one row's outcome into shape-checked per-question gold,
    restricted to the questions the row asked, alongside the matching
    question subset. ``None`` when the outcome has no usable gold, the
    intersection is empty, or any gold fails its question-type check."""
    gold = gold_for(pilot, outcome)
    if gold is None:
        logger.debug(
            "export: skipping row without usable gold",
            pilot=pilot,
            outcome=outcome,
            row_id=row_id,
        )
        return None
    # Export only the intersection: question keys the row asked AND the
    # registry labels. Neither side may inject keys into the other.
    labeled = example_gold(gold, questions)
    kept_questions = {key: questions[key] for key in labeled}
    if not labeled or not kept_questions:
        return None
    probabilities_by_key: dict[str, dict[str, float]] = {}
    for key, probability_map in labeled.items():
        checked = _checked_probabilities(
            str(kept_questions[key].get("type", "")), probability_map
        )
        if checked is None:
            logger.warning(
                "export: gold failed its question-type shape check; row skipped",
                pilot=pilot,
                outcome=outcome,
                row_id=row_id,
                question_key=key,
            )
            return None
        probabilities_by_key[key] = checked
    return probabilities_by_key, kept_questions


def build_example(row: Any) -> dict[str, Any] | None:
    """One corpus line from a decision_log row, or ``None`` to skip.

    Skips (with a logged reason, never silently): rows missing inputs or
    outcome, outcomes with no registered gold for the pilot, gold that
    labels a question the row never asked, and gold that fails its
    question-type shape check.
    """
    state = getattr(row, "state", None)
    questions = getattr(row, "questions", None)
    outcome = getattr(row, "outcome", None)
    if not state or not questions or not outcome:
        return None
    pilot = str(getattr(row, "pilot", ""))
    row_id = str(getattr(row, "id", ""))
    resolved = _labeled_gold(row_id, pilot, str(outcome), questions)
    if resolved is None:
        return None
    probabilities_by_key, kept_questions = resolved
    return {
        "id": row_id,
        "workflow": pilot,
        "state": json.dumps(state, ensure_ascii=False, default=str),
        "questions": json.dumps(kept_questions, ensure_ascii=False, default=str),
        "gold": json.dumps(
            {
                key: {"probabilities": probability_map}
                for key, probability_map in probabilities_by_key.items()
            },
            ensure_ascii=False,
        ),
    }


def _select_labeled(pilot: str | None, limit: int) -> Any:
    """The labeled-rows query: oldest-first for reproducible splits;
    ``limit`` bounds the file (rows are appended by later runs, never
    re-selected, once labeled rows are pruned by retention)."""
    query = (
        select(DecisionLogTable)
        .where(
            DecisionLogTable.outcome.is_not(None),
            DecisionLogTable.state.is_not(None),
            DecisionLogTable.questions.is_not(None),
        )
        .order_by(DecisionLogTable.created_at.asc(), DecisionLogTable.id.asc())
    )
    if pilot:
        query = query.where(DecisionLogTable.pilot == pilot)
    if limit > 0:
        query = query.limit(limit)
    return query


def _write_examples(rows: Any, out_path: str) -> int:
    """Write one JSONL line per example-built row, atomically: buffer to a
    temp file in the target directory and rename only on success, so a
    failed export never leaves a truncated corpus file behind."""
    out = Path(out_path)
    handle, temp_name = tempfile.mkstemp(
        dir=out.parent, prefix=".corpus-", suffix=".jsonl"
    )
    temp_path = Path(temp_name)
    written = 0
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            for row in rows:
                example = build_example(row)
                if example is None:
                    continue
                fh.write(json.dumps(example, ensure_ascii=False) + "\n")
                written += 1
        temp_path.replace(out)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise
    return written


async def export_corpus(out_path: str, pilot: str | None, limit: int) -> int:
    """Stream labeled rows to ``out_path`` as JSONL. Returns the row count."""
    from roboco.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(_select_labeled(pilot, limit))
        written = _write_examples(rows.scalars(), out_path)
    logger.info("export: corpus written", path=out_path, rows=written)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m roboco.services.decisions.export",
        description=(
            "Export labeled decision_log rows into the laya fine-tune "
            "corpus format (JSONL: id/workflow/state/questions/gold)."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output JSONL path (written atomically; requires a labeled row).",
    )
    parser.add_argument(
        "--pilot",
        default=None,
        help="Restrict the export to one pilot slug (e.g. self_heal).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50_000,
        help="Maximum rows to select (default 50000, oldest first).",
    )
    args = parser.parse_args(argv)
    written = asyncio.run(
        export_corpus(args.out, args.pilot, max(args.limit, 0))
    )
    print(f"wrote {written} labeled examples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
