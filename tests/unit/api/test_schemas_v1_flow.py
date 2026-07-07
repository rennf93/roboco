"""Schema-level tests for v1 flow request bodies."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.flow import (
    DelegateRequest,
    IWillPlanRequest,
    IWillWorkOnRequest,
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


def test_i_will_plan_request_rejects_overlong_approach() -> None:
    """approach >800 chars is rejected — no ceiling was the bloat bug."""
    with pytest.raises(ValidationError) as exc:
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 801,
        )
    assert "approach" in str(exc.value)


def test_i_will_plan_request_rejects_overlong_subtask_title() -> None:
    with pytest.raises(ValidationError):
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 150,
            sub_tasks=[{"title": "t" * 201, "description": "d" * 30}],
        )


def test_i_will_plan_request_rejects_overlong_subtask_description() -> None:
    with pytest.raises(ValidationError):
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 150,
            sub_tasks=[{"title": "ok title", "description": "d" * 601}],
        )


def test_i_will_plan_request_rejects_thin_subtask_description() -> None:
    """A sub_task description <20 chars fails the typed SubTaskCreate model."""
    with pytest.raises(ValidationError):
        IWillPlanRequest(
            task_id=uuid4(),
            plan="plan",
            approach="a" * 150,
            sub_tasks=[{"title": "ok title", "description": "too short"}],
        )


def test_delegate_request_rejects_overlong_acceptance_criterion() -> None:
    """An AC item >200 chars is rejected — a criterion that long is a restated
    description, not a verifiable outcome."""
    long_ac = "x" * 201
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
                "acceptance_criteria": [long_ac],
            }
        )
    assert "acceptance_criteria" in str(exc.value)


def test_delegate_request_rejects_too_many_acceptance_criteria() -> None:
    """An AC list >7 items is rejected — over-decomposition of criteria."""
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
