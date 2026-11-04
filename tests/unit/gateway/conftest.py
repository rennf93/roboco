"""Gateway unit-test fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from roboco.services.gateway.choreographer import dev as dev_module


@dataclass
class GateLaunch:
    task_id: Any
    run: Any
    finish: Any
    timeout_seconds: float


class RecordingGateRegistry:
    """Stands in for the in-process gate-job registry: records launches and
    never starts a real background job (which would need a database)."""

    def __init__(self) -> None:
        self.launches: list[GateLaunch] = []
        self.running = False

    def is_running(self, _task_id: Any) -> bool:
        return self.running

    def launch(
        self, task_id: Any, run: Any, finish: Any, *, timeout_seconds: float
    ) -> bool:
        if self.running:
            return False
        self.launches.append(GateLaunch(task_id, run, finish, timeout_seconds))
        return True


@pytest.fixture(autouse=True)
def gate_registry(monkeypatch: pytest.MonkeyPatch) -> RecordingGateRegistry:
    registry = RecordingGateRegistry()
    monkeypatch.setattr(dev_module, "get_gate_job_registry", lambda: registry)
    return registry
