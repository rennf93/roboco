"""Tests for the standardized response envelope."""

from __future__ import annotations

from uuid import uuid4

from roboco.services.gateway.envelope import Envelope


class TestEnvelopeOk:
    def test_ok_minimal_response(self) -> None:
        env = Envelope.ok(
            status="in_progress", task_id=str(uuid4()), next="edit + commit"
        )
        body = env.as_dict()
        assert body["status"] == "in_progress"
        assert body["next"] == "edit + commit"
        assert body["error"] is None
        assert body["evidence"] is None or body["evidence"] == {}
        assert "context_briefing" in body

    def test_ok_with_evidence(self) -> None:
        evidence = {"pr_url": "https://github.com/x/y/pull/8", "commits": []}
        env = Envelope.ok(
            status="awaiting_qa", task_id=str(uuid4()), next="idle", evidence=evidence
        )
        assert env.as_dict()["evidence"] == evidence


class TestEnvelopeError:
    def test_tracing_gap(self) -> None:
        env = Envelope.tracing_gap(
            missing=["progress>=1", "journal:reflect"],
            remediate="call note(scope='reflect', task_id='...')",
        )
        body = env.as_dict()
        assert body["error"] == "tracing_gap"
        assert body["missing"] == ["progress>=1", "journal:reflect"]
        assert "note(scope='reflect'" in body["remediate"]

    def test_invalid_state(self) -> None:
        env = Envelope.invalid_state(
            message="task is blocked", remediate="wait for PM unblock"
        )
        body = env.as_dict()
        assert body["error"] == "invalid_state"
        assert body["message"] == "task is blocked"

    def test_not_authorized(self) -> None:
        env = Envelope.not_authorized(message="role mismatch", remediate="claim first")
        assert env.as_dict()["error"] == "not_authorized"
