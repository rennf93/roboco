"""Schema-level tests for v1 flow request bodies."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.flow import (
    _APPROACH_MAX_CHARS,
    DelegateRequest,
    IWillPlanRequest,
    IWillWorkOnRequest,
    OpenQuestionCreate,
    RiskCreate,
    SubTaskCreate,
)
from roboco.models.base import Complexity


def test_delegate_request_requires_task_type() -> None:
    """task_type must be supplied explicitly — no magic default.

    Background: the 2026-05-08 smoke-test trace showed main-pm calling
    delegate without task_type, the schema defaulted to 'code', the
    cell PM downstream couldn't plan a code-typed parent (pre-fix), and
    the run deadlocked. Make the field required so misuse fails at the
    HTTP boundary with a clear 422.
    """
    with pytest.raises(ValidationError) as exc:
        DelegateRequest.model_validate(
            {
                "parent_task_id": uuid4(),
                "title": "t",
                "description": "add the new endpoint plus tests",
                "assigned_to": "be-dev-1",
                "team": "backend",
                "nature": "technical",
                "estimated_complexity": "medium",
                "acceptance_criteria": ["returns 200"],
                # task_type intentionally omitted
            }
        )
    assert "task_type" in str(exc.value)


def test_delegate_request_accepts_explicit_task_type() -> None:
    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="t",
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity=Complexity.MEDIUM,
        acceptance_criteria=["returns 200"],
    )
    assert req.task_type == "code"


# ---------------------------------------------------------------------------
# StrList — SDK-nested list-of-strings coercion (Bug A)
# ---------------------------------------------------------------------------


def test_i_will_plan_request_flattens_sdk_nested_technical_considerations() -> None:
    """The Claude SDK parses XML-ish ``<item>…</item>`` list-of-strings tool
    input into nested arrays (``[[["…"]]]``). A bare ``list[str]`` field
    hard-rejects element 1 (a list, not a str) at validation time — the live
    ``i_will_plan`` crash: ``technical_considerations.1 Input should be a
    valid string``. The ``StrList`` BeforeValidator must flatten it to a flat
    ``list[str]`` so the verb body receives clean strings.
    """
    # The SDK nests list-of-strings tool input as nested arrays / dict-wrapped
    # text (``[[["…"]]]``, ``{"item": {"$text": "…"}}``). Annotated ``list[Any]``
    # so mypy accepts the coerce-able shape; the ``StrList`` BeforeValidator
    # flattens it to ``list[str]`` at runtime (no ``type: ignore`` owed).
    technical_considerations: list[Any] = [
        [[["Empty state distinct from loaded state, coverage target 80%"]]],
        [{"item": {"$text": "Use asyncpg prepared statements"}}],
    ]
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="Plan narrative describing the approach in full sentences.",
        approach=(
            "Approach text long enough to clear the 150-character minimum "
            "enforced on the plan's Approach field so the Plan tab is fully "
            "populated for audit and tracing instead of rendering an empty view."
        ),
        technical_considerations=technical_considerations,
    )
    assert req.technical_considerations == [
        "Empty state distinct from loaded state, coverage target 80%",
        "Use asyncpg prepared statements",
    ]


def test_i_will_work_on_request_flattens_dict_wrapped_technical_considerations() -> (
    None
):
    """Same coercion on the developer planning verb — a dict-wrapped string
    (``{"item": {"$text": "…"}}``, the SDK's element-text marker) must reduce
    to the bare string, not ``str(dict)``."""
    technical_considerations: list[Any] = [
        {"item": {"$text": "Cache the lookup result"}}
    ]
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        technical_considerations=technical_considerations,
    )
    assert req.technical_considerations == ["Cache the lookup result"]


def test_delegate_request_flattens_sdk_nested_acceptance_criteria() -> None:
    """``delegate``'s ``acceptance_criteria`` is the same list-of-strings shape
    the SDK can nest (this is the ``delegate``-verb analogue of the MegaTask
    Bug 3 crash). The ``StrList`` field must flatten the nested input so the
    VARCHAR[] insert downstream never sees a dict/list element."""
    acceptance_criteria: list[Any] = [
        [[["returns 200 for valid input"]]],
        [{"item": {"$text": "rejects malformed input with 400"}}],
    ]
    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="t",
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity=Complexity.MEDIUM,
        acceptance_criteria=acceptance_criteria,
    )
    assert req.acceptance_criteria == [
        "returns 200 for valid input",
        "rejects malformed input with 400",
    ]


def test_strlist_drops_non_string_junk_instead_of_crashing() -> None:
    """Non-string junk (a bare int, a dict with no string values, whitespace)
    is dropped — the field never raises on garbage the SDK might emit; only
    real strings survive. An all-junk payload yields an empty list (the
    delegate min_length=1 gate then rejects it cleanly, not a 500)."""
    technical_considerations: list[Any] = [42, {"foo": 123}, [["  "]], "real note"]
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        technical_considerations=technical_considerations,
    )
    assert req.technical_considerations == ["real note"]


# ---------------------------------------------------------------------------
# Task-content guardrails (2026-07-07): ceilings on plan content + AC caps.
# ---------------------------------------------------------------------------


def test_i_will_plan_request_rejects_overlong_plan() -> None:
    """plan >2000 chars is rejected at the boundary — the bloat defect."""
    with pytest.raises(ValidationError) as exc:
        IWillPlanRequest(
            task_id=uuid4(),
            plan="x" * 2001,
            approach="a" * 150,
        )
    assert "plan" in str(exc.value)


def test_i_will_plan_request_truncates_overlong_approach() -> None:
    """approach >800 chars is truncated to 800 (797 + "..."), never rejected —
    a hard 422 here used to throw away an otherwise-good plan and degrade the
    PM to a bare unblock (live incident)."""
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="plan",
        approach="a" * (_APPROACH_MAX_CHARS + 1),
    )
    assert len(req.approach) == _APPROACH_MAX_CHARS
    assert req.approach.endswith("...")
    assert req.approach == "a" * (_APPROACH_MAX_CHARS - 3) + "..."


def test_i_will_plan_request_approach_exactly_800_untruncated() -> None:
    """Exactly the ceiling is the boundary — passes through byte-for-byte."""
    approach = "a" * _APPROACH_MAX_CHARS
    req = IWillPlanRequest(task_id=uuid4(), plan="plan", approach=approach)
    assert req.approach == approach
    assert not req.approach.endswith("...")


def test_i_will_plan_request_rejects_thin_approach() -> None:
    """approach <150 chars is still a hard reject — a thin plan IS a defect."""
    with pytest.raises(ValidationError) as exc:
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 149,
        )
    assert "approach" in str(exc.value)


def test_i_will_plan_request_truncates_overlong_subtask_title() -> None:
    """title >200 chars is truncated to 200 (197 + "..."), never rejected —
    same treatment as approach (a 422 here threw away an otherwise-good plan,
    the 2026-08 live incident)."""
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="plan",
        approach="a" * 150,
        sub_tasks=[SubTaskCreate(title="t" * 201, description="d" * 30)],
    )
    assert req.sub_tasks[0].title == "t" * 197 + "..."


def test_i_will_plan_request_truncates_overlong_subtask_description() -> None:
    """description >600 chars is truncated to 600, never rejected."""
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="plan",
        approach="a" * 150,
        sub_tasks=[SubTaskCreate(title="ok title", description="d" * 601)],
    )
    assert req.sub_tasks[0].description == "d" * 597 + "..."


def test_i_will_plan_request_rejects_thin_subtask_description() -> None:
    """A sub_task description <20 chars fails the typed SubTaskCreate model."""
    with pytest.raises(ValidationError):
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 150,
            sub_tasks=[SubTaskCreate(title="ok title", description="too short")],
        )


def test_delegate_request_truncates_overlong_acceptance_criterion() -> None:
    """An AC item >200 chars is truncated to 200, never rejected — the list
    count cap (<=7) stays a hard reject (dropping ACs is data loss); a
    verbose criterion's tail truncating is not."""
    long_ac = "x" * 250
    req = DelegateRequest.model_validate(
        {
            "parent_task_id": uuid4(),
            "title": "t",
            "description": "add the new endpoint plus tests",
            "assigned_to": "be-dev-1",
            "team": "backend",
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
            "acceptance_criteria": [long_ac],
        }
    )
    assert req.acceptance_criteria == ["x" * 197 + "..."]


def test_delegate_request_rejects_too_many_acceptance_criteria() -> None:
    """An AC list >7 items is STILL rejected — dropping ACs silently would
    be data loss, unlike truncating one verbose item's tail."""
    with pytest.raises(ValidationError) as exc:
        DelegateRequest.model_validate(
            {
                "parent_task_id": uuid4(),
                "title": "t",
                "description": "add the new endpoint plus tests",
                "assigned_to": "be-dev-1",
                "team": "backend",
                "task_type": "code",
                "nature": "technical",
                "estimated_complexity": "medium",
                "acceptance_criteria": [f"criterion {i}" for i in range(8)],
            }
        )
    assert "acceptance_criteria" in str(exc.value)


def test_delegate_request_truncates_overlong_title() -> None:
    """DelegateRequest.title >200 chars is truncated to 200, never rejected."""
    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="t" * 250,
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity=Complexity.MEDIUM,
        acceptance_criteria=["returns 200"],
    )
    assert req.title == "t" * 197 + "..."


def test_risk_create_truncates_overlong_risk_and_mitigation() -> None:
    risk = RiskCreate(risk="r" * 350, mitigation="m" * 650)
    assert risk.risk == "r" * 297 + "..."
    assert risk.mitigation == "m" * 597 + "..."


def test_open_question_create_truncates_overlong_question() -> None:
    q = OpenQuestionCreate(question="q" * 350)
    assert q.question == "q" * 297 + "..."


def test_short_valid_fields_pass_through_unchanged() -> None:
    """No accidental mutation: input at/under the caps is untouched."""
    sub_task = SubTaskCreate(title="ok title", description="d" * 30)
    assert sub_task.title == "ok title"
    assert sub_task.description == "d" * 30

    risk = RiskCreate(risk="short risk", mitigation="short mitigation")
    assert risk.risk == "short risk"
    assert risk.mitigation == "short mitigation"

    question = OpenQuestionCreate(question="is this in scope?")
    assert question.question == "is this in scope?"

    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="Add user lookup endpoint",
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity=Complexity.MEDIUM,
        acceptance_criteria=["returns 200 for valid input"],
    )
    assert req.title == "Add user lookup endpoint"
    assert req.acceptance_criteria == ["returns 200 for valid input"]


# ---------------------------------------------------------------------------
# IWillWorkOnRequest.steps/risks/open_questions (2026-08 fast-follow): same
# incident class as the PM-side fields above, but these stayed plain
# list[dict[str, str]] — the choreographer's _thin_subtask_hint gate applies
# the SAME 200/600/300/600/300-char limits to a dev's steps/risks/
# open_questions as it does to a PM's sub_tasks/risks/open_questions, so an
# overlong dev field hard-stranded i_will_work_on exactly like an overlong
# PM sub_task did.
# ---------------------------------------------------------------------------


def test_i_will_work_on_request_truncates_overlong_step_description() -> None:
    """A step description >600 chars is clamped to 600, never rejected."""
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        steps=[{"title": "ok title", "description": "d" * 601}],
    )
    assert req.steps[0]["description"] == "d" * 597 + "..."
    assert req.steps[0]["title"] == "ok title"


def test_i_will_work_on_request_truncates_overlong_step_title() -> None:
    """A step title >200 chars is clamped to 200, never rejected."""
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        steps=[{"title": "t" * 201, "description": "d" * 30}],
    )
    assert req.steps[0]["title"] == "t" * 197 + "..."


def test_i_will_work_on_request_truncates_overlong_risk() -> None:
    """risk/mitigation entries clamp exactly like RiskCreate."""
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        risks=[{"risk": "r" * 350, "mitigation": "m" * 650}],
    )
    assert req.risks[0]["risk"] == "r" * 297 + "..."
    assert req.risks[0]["mitigation"] == "m" * 597 + "..."


def test_i_will_work_on_request_truncates_overlong_open_question() -> None:
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        open_questions=[{"question": "q" * 350, "answered": False}],
    )
    assert req.open_questions[0]["question"] == "q" * 297 + "..."
    # non-str value on a known/unclamped key passes through untouched.
    assert req.open_questions[0]["answered"] is False


def test_i_will_work_on_request_short_fields_pass_through_unchanged() -> None:
    """No accidental mutation: input at/under the caps is untouched."""
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        steps=[{"title": "ok title", "description": "d" * 30}],
        risks=[{"risk": "short risk", "mitigation": "short mitigation"}],
        open_questions=[{"question": "is this in scope?", "answered": False}],
    )
    assert req.steps == [{"title": "ok title", "description": "d" * 30}]
    assert req.risks == [{"risk": "short risk", "mitigation": "short mitigation"}]
    assert req.open_questions == [{"question": "is this in scope?", "answered": False}]


def test_i_will_work_on_request_non_dict_step_still_reported_by_real_validation() -> (
    None
):
    """A malformed (non-dict) entry is left alone by the clamp — it still
    hits the real ``list[dict[str, str]]`` type check and raises, so the
    clamp can't mask a genuinely malformed payload."""
    with pytest.raises(ValidationError) as exc:
        IWillWorkOnRequest(
            task_id=uuid4(),
            steps=[{"title": "t"}, "not a dict"],  # type: ignore[list-item]
        )
    assert "steps" in str(exc.value)
