"""Unit tests for the Decisions wire schemas (lenient parsing)."""

from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    DecisionAnswer,
    NoulQuestion,
    ScoreQuestion,
    parse_decisions_payload,
)


class TestParseLeniently:
    def test_full_payload_parses_all_three_types(self) -> None:
        payload = {
            "id": "gen-dec-1",
            "model": "typesafe/jev-1.13-20260917",
            "answers": {
                "is_transient": {"type": "noul", "noul": 0.96},
                "routing_tier": {
                    "type": "choice",
                    "choice": "park",
                    "confidence": 0.75,
                    "probabilities": {"park": 0.9, "retry_soon": 0.1},
                },
                "complexity": {
                    "type": "score",
                    "score": 1.0,
                    "confidence": 0.99,
                },
            },
            "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.00002},
        }
        result = parse_decisions_payload(
            payload, tier="openrouter", session_id="selfheal:run-1"
        )
        assert result.model == "typesafe/jev-1.13-20260917"
        assert result.tier == "openrouter"
        assert result.usage.cost == 0.00002
        is_transient = result.answer("is_transient")
        assert is_transient is not None
        assert is_transient.noul == 0.96
        routing_tier = result.answer("routing_tier")
        assert routing_tier is not None
        assert routing_tier.choice == "park"
        assert routing_tier.probabilities == {
            "park": 0.9,
            "retry_soon": 0.1,
        }
        complexity = result.answer("complexity")
        assert complexity is not None
        assert complexity.score == 1.0

    def test_unknown_answer_keys_are_ignored(self) -> None:
        payload = {
            "answers": {
                "gate": {"type": "noul", "noul": 0.9, "brand_new_field": [1, 2]},
                "extra_answer": {"type": "something_new", "x": 1},
            }
        }
        result = parse_decisions_payload(payload, tier="laya", session_id="s")
        gate = result.answer("gate")
        assert gate is not None
        assert gate.noul == 0.9
        extra_answer = result.answer("extra_answer")
        assert extra_answer is not None
        assert extra_answer.type == "something_new"
        assert extra_answer.verdict() is None

    def test_missing_confidence_is_none_fail_open(self) -> None:
        payload = {"answers": {"gate": {"type": "choice", "choice": "escalate"}}}
        result = parse_decisions_payload(payload, tier="laya", session_id="s")
        gate = result.answer("gate")
        assert gate is not None
        assert gate.confidence is None

    def test_non_dict_answer_entry_is_skipped(self) -> None:
        payload = {"answers": {"gate": {"type": "noul", "noul": 0.9}, "bad": "oops"}}
        result = parse_decisions_payload(payload, tier="laya", session_id="s")
        assert set(result.answers) == {"gate"}

    def test_missing_usage_and_model_tolerated(self) -> None:
        result = parse_decisions_payload({"answers": {}}, tier="laya", session_id="s")
        assert result.model is None
        assert result.usage.cost is None

    def test_non_numeric_confidence_becomes_none(self) -> None:
        payload = {
            "answers": {"gate": {"type": "noul", "noul": "high", "confidence": "very"}}
        }
        result = parse_decisions_payload(payload, tier="laya", session_id="s")
        gate = result.answer("gate")
        assert gate is not None
        assert gate.noul is None
        assert gate.confidence is None

    def test_raw_payload_preserved_for_logs(self) -> None:
        payload = {"answers": {"gate": {"type": "noul", "noul": 0.9}}}
        result = parse_decisions_payload(payload, tier="laya", session_id="s")
        gate = result.answer("gate")
        assert gate is not None
        assert gate.raw_payload == {"type": "noul", "noul": 0.9}
        assert result.raw == payload


class TestAnswerVerdict:
    def test_verdict_by_type(self) -> None:
        assert DecisionAnswer(key="a", type="noul", noul=0.5).verdict() == 0.5
        assert DecisionAnswer(key="b", type="choice", choice="park").verdict() == "park"
        assert DecisionAnswer(key="c", type="score", score=2.0).verdict() == 2.0
        assert DecisionAnswer(key="d", type="future").verdict() is None


class TestQuestionModels:
    def test_wire_shape_round_trip(self) -> None:
        noul = NoulQuestion(instructions="Is this transient?")
        choice = ChoiceQuestion(
            instructions="Which lane?", criteria={"a": "alpha", "b": "beta"}
        )
        score = ScoreQuestion(instructions="How heavy?", criteria=["light", "std"])
        assert noul.model_dump(exclude_none=True) == {
            "type": "noul",
            "instructions": "Is this transient?",
        }
        assert choice.model_dump(exclude_none=True)["criteria"] == {
            "a": "alpha",
            "b": "beta",
        }
        assert score.model_dump(exclude_none=True)["criteria"] == ["light", "std"]
