"""models.events Event class coverage."""

from __future__ import annotations

from uuid import uuid4

from roboco.events.bus import Event, EventType


def test_event_default_id_and_timestamp() -> None:
    e = Event(type=EventType.TASK_CREATED, data={})
    assert e.id is not None
    assert e.timestamp is not None


def test_event_to_json_round_trip() -> None:
    original = Event(
        type=EventType.TASK_BLOCKED,
        data={"reason": "x"},
        source_agent="be-dev-1",
        correlation_id="abc",
    )
    json_str = original.to_json()
    restored = Event.from_json(json_str)
    assert restored.id == original.id
    assert restored.type == original.type
    assert restored.data == original.data
    assert restored.source_agent == original.source_agent
    assert restored.correlation_id == original.correlation_id


def test_event_to_json_no_optional_fields() -> None:
    e = Event(type=EventType.TASK_CREATED, data={})
    json_str = e.to_json()
    restored = Event.from_json(json_str)
    assert restored.source_agent is None
    assert restored.correlation_id is None


def test_event_with_explicit_id() -> None:
    eid = uuid4()
    e = Event(type=EventType.TASK_BLOCKED, data={}, id=eid)
    assert e.id == eid
