"""SandboxProvisioner: throwaway per-spawn Postgres/Redis sibling containers.

All docker calls are mocked — no real docker in unit tests. Readiness
deadlines are monkeypatched down so the timeout path runs in milliseconds.
"""

from __future__ import annotations

import time

import pytest
from roboco.runtime import sandbox as sandbox_module
from roboco.runtime.sandbox import SandboxProvisioner, SandboxProvisionError

_NETWORK = "roboco_default"
_PG_PORT = 5432
_REDIS_PORT = 6379


class _FakeRunner:
    """Records every docker invocation; behavior configured per test."""

    def __init__(
        self,
        *,
        run_rc: int = 0,
        exec_rc: int = 0,
        teardown_rc: int = 0,
        ps_output: bytes = b"",
        ps_live_output: bytes = b"",
    ) -> None:
        self.calls: list[list[str]] = []
        self.run_rc = run_rc
        self.exec_rc = exec_rc
        self.teardown_rc = teardown_rc
        self.ps_output = ps_output
        self.ps_live_output = ps_live_output
        self._ps_call_count = 0

    async def __call__(
        self, args: list[str], _timeout: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(args)
        verb = args[0]
        if verb == "run":
            return self.run_rc, b"container-id\n", b""
        if verb == "exec":
            return self.exec_rc, b"", b""
        if verb in ("stop", "kill", "rm"):
            return self.teardown_rc, b"", b""
        if verb == "ps":
            self._ps_call_count += 1
            # First ps call = the sandbox-labeled listing; second = live agents.
            if self._ps_call_count == 1:
                return 0, self.ps_output, b""
            return 0, self.ps_live_output, b""
        raise AssertionError(f"unexpected docker verb: {verb}")


@pytest.fixture(autouse=True)
def _fast_readiness_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the polling deadlines so the timeout path is fast in tests."""
    monkeypatch.setattr(sandbox_module, "_PG_READY_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(sandbox_module, "_REDIS_READY_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(sandbox_module, "_READY_POLL_INTERVAL_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_provision_both_services_happy_path() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    info = await provisioner.provision("dev-1", ["postgres", "redis"])

    assert info.postgres is not None
    assert info.postgres.host == "roboco-sandbox-pg-dev-1"
    assert info.postgres.port == _PG_PORT
    assert info.postgres.user == "sandbox"
    assert info.postgres.database == "sandbox"
    assert info.redis is not None
    assert info.redis.host == "roboco-sandbox-redis-dev-1"
    assert info.redis.port == _REDIS_PORT
    # Passwords are per-sandbox random tokens, not equal to each other.
    assert info.postgres.password != info.redis.password


@pytest.mark.asyncio
async def test_provision_labels_are_correct() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.provision("dev-2", ["postgres"])

    run_call = next(c for c in runner.calls if c[0] == "run")
    assert "--network" in run_call
    assert run_call[run_call.index("--network") + 1] == _NETWORK
    label_indices = [i for i, a in enumerate(run_call) if a == "--label"]
    labels = [run_call[i + 1] for i in label_indices]
    assert sandbox_module.SANDBOX_LABEL in labels
    assert "roboco.sandbox.owner=roboco-agent-dev-2" in labels


@pytest.mark.asyncio
async def test_provision_readiness_timeout_tears_down_and_raises() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=1)  # container starts, never ready
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    with pytest.raises(SandboxProvisionError):
        await provisioner.provision("dev-3", ["postgres"])

    # Teardown attempted for the container that failed readiness.
    teardown_verbs = {c[0] for c in runner.calls if c[0] in ("stop", "kill", "rm")}
    assert "stop" in teardown_verbs or "rm" in teardown_verbs
    rm_calls = [c for c in runner.calls if c[0] == "rm"]
    assert any("roboco-sandbox-pg-dev-3" in c for c in rm_calls)


@pytest.mark.asyncio
async def test_provision_run_failure_tears_down_and_raises() -> None:
    runner = _FakeRunner(run_rc=1)  # docker run itself fails
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    with pytest.raises(SandboxProvisionError):
        await provisioner.provision("dev-4", ["redis"])


@pytest.mark.asyncio
async def test_provision_rejects_unknown_service() -> None:
    runner = _FakeRunner()
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    with pytest.raises(SandboxProvisionError):
        await provisioner.provision("dev-5", ["mysql"])
    # Nothing was ever run for an unknown service.
    assert runner.calls == []


@pytest.mark.asyncio
async def test_teardown_idempotent_on_missing_container() -> None:
    runner = _FakeRunner(teardown_rc=1)  # "no such container" for every verb
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    # Must not raise even though every teardown call reports failure.
    await provisioner.teardown("never-provisioned")

    verbs = [c[0] for c in runner.calls]
    assert "rm" in verbs


@pytest.mark.asyncio
async def test_janitor_removes_orphaned_sandbox_only() -> None:
    # Two sandboxes on the host: one owned by a still-live agent, one orphaned.
    ps_output = (
        b"roboco-sandbox-pg-alive\troboco-agent-alive\n"
        b"roboco-sandbox-pg-orphan\troboco-agent-orphan\n"
    )
    live_output = b"roboco-agent-alive\n"
    runner = _FakeRunner(ps_output=ps_output, ps_live_output=live_output)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.janitor_sweep()

    rm_calls = [c for c in runner.calls if c[0] == "rm"]
    torn_down = {c[-1] for c in rm_calls}
    assert "roboco-sandbox-pg-orphan" in torn_down
    assert "roboco-sandbox-pg-alive" not in torn_down


@pytest.mark.asyncio
async def test_janitor_noop_when_no_sandboxes() -> None:
    runner = _FakeRunner(ps_output=b"")
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.janitor_sweep()

    assert all(c[0] != "rm" for c in runner.calls)


@pytest.mark.asyncio
async def test_provision_preclears_stale_sandboxes_before_run() -> None:
    """A crash-missed teardown leaves same-named containers; provision must
    clear them first or `docker run` fails on the name conflict."""
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.provision("dev-6", ["postgres"])

    first_run = next(i for i, c in enumerate(runner.calls) if c[0] == "run")
    preclear_rms = [
        c for c in runner.calls[:first_run] if c[0] == "rm" and c[-1].endswith("dev-6")
    ]
    assert any("roboco-sandbox-pg-dev-6" in c for c in preclear_rms)
    assert any("roboco-sandbox-redis-dev-6" in c for c in preclear_rms)


@pytest.mark.asyncio
async def test_janitor_grace_skips_freshly_provisioned_owner() -> None:
    """A sandbox is provisioned before its agent container exists — a sweep
    racing that mid-flight spawn must not reap the fresh sandbox."""
    ps_output = b"roboco-sandbox-pg-fresh\troboco-agent-fresh\n"
    runner = _FakeRunner(ps_output=ps_output, ps_live_output=b"")
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)
    provisioner._provisioned_at = {"roboco-agent-fresh": time.monotonic()}

    await provisioner.janitor_sweep()

    assert all(c[0] != "rm" for c in runner.calls)


@pytest.mark.asyncio
async def test_janitor_reaps_after_grace_expiry() -> None:
    ps_output = b"roboco-sandbox-pg-old\troboco-agent-old\n"
    runner = _FakeRunner(ps_output=ps_output, ps_live_output=b"")
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)
    provisioner._provisioned_at = {
        "roboco-agent-old": time.monotonic()
        - 10 * sandbox_module._JANITOR_GRACE_SECONDS
    }

    await provisioner.janitor_sweep()

    rm_calls = [c for c in runner.calls if c[0] == "rm"]
    assert any("roboco-sandbox-pg-old" in c for c in rm_calls)
    assert provisioner._provisioned_at == {}
