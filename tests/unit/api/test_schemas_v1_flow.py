"""Schema-level tests for v1 flow request bodies."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.flow import (
    DelegateRequest,
    IWillPlanRequest,
    IWillWorkOnRequest,
)


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
        estimated_complexity="medium",
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
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="Plan narrative describing the approach in full sentences.",
        approach=(
            "Approach text long enough to clear the 150-character minimum "
            "enforced on the plan's Approach field so the Plan tab is fully "
            "populated for audit and tracing instead of rendering an empty view."
        ),
        technical_considerations=[
            [[["Empty state distinct from loaded state, coverage target 80%"]]],
            [{"item": {"$text": "Use asyncpg prepared statements"}}],
        ],
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
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        technical_considerations=[{"item": {"$text": "Cache the lookup result"}}],
    )
    assert req.technical_considerations == ["Cache the lookup result"]


def test_delegate_request_flattens_sdk_nested_acceptance_criteria() -> None:
    """``delegate``'s ``acceptance_criteria`` is the same list-of-strings shape
    the SDK can nest (this is the ``delegate``-verb analogue of the MegaTask
    Bug 3 crash). The ``StrList`` field must flatten the nested input so the
    VARCHAR[] insert downstream never sees a dict/list element."""
    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="t",
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
        acceptance_criteria=[
            [[["returns 200 for valid input"]]],
            [{"item": {"$text": "rejects malformed input with 400"}}],
        ],
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
    req = IWillWorkOnRequest(
        task_id=uuid4(),
        technical_considerations=[42, {"foo": 123}, [["  "]], "real note"],
    )
    assert req.technical_considerations == ["real note"]
