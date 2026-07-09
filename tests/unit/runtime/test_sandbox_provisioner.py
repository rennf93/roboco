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
_MONGO_PORT = 27017


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
        # Image state — defaults assume the image is already present (the happy
        # path skips the pull). Tests exercising the pull path override these.
        self.image_present: bool = True
        self.pull_rc: int = 0
        # is_live()'s `docker inspect --format={{.State.Running}}` fake —
        # set post-construction, mirroring image_present/pull_rc above.
        self.inspect_rc: int = 0
        self.inspect_running: bool = True
        self._ps_call_count = 0

    async def __call__(
        self, args: list[str], _timeout: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(args)
        verb = args[0]
        if verb == "run":
            rc, out, err = self.run_rc, b"container-id\n", b""
        elif verb == "exec":
            rc, out, err = self.exec_rc, b"", b""
        elif verb in ("stop", "kill", "rm"):
            rc, out, err = self.teardown_rc, b"", b""
        elif verb == "image":
            # `image inspect <img>` — rc 0 means present (skip pull).
            if args[1] != "inspect":
                raise AssertionError(f"unexpected image subverb: {args[1]}")
            rc, out, err = (0 if self.image_present else 1), b"", b""
        elif verb == "pull":
            rc = self.pull_rc
            out, err = b"", (b"" if rc == 0 else b"pull failed\n")
        elif verb == "ps":
            self._ps_call_count += 1
            # First ps call = the sandbox-labeled listing; second = live agents.
            listing = (
                self.ps_output if self._ps_call_count == 1 else self.ps_live_output
            )
            rc, out, err = 0, listing, b""
        elif verb == "inspect":
            rc = self.inspect_rc
            out = b"true\n" if self.inspect_running else b"false\n"
            err = b""
        else:
            raise AssertionError(f"unexpected docker verb: {verb}")
        return rc, out, err


@pytest.fixture(autouse=True)
def _fast_readiness_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the polling cadence + every engine's deadline so the timeout path
    is fast in tests. Deadlines live on the engine instances now (not module
    constants), so monkeypatch them on the registry."""
    monkeypatch.setattr(sandbox_module, "_READY_POLL_INTERVAL_SECONDS", 0.01)
    for engine in sandbox_module.SANDBOX_ENGINES.values():
        monkeypatch.setattr(engine, "ready_deadline", 0.05)


@pytest.mark.asyncio
async def test_provision_both_services_happy_path() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    info = await provisioner.provision("dev-1", ["postgres", "redis"])

    pg = info.services["postgres"]
    assert pg.host == "roboco-sandbox-pg-dev-1"
    assert pg.port == _PG_PORT
    assert pg.user == "sandbox"
    assert pg.database == "sandbox"
    rd = info.services["redis"]
    assert rd.host == "roboco-sandbox-redis-dev-1"
    assert rd.port == _REDIS_PORT
    # Passwords are per-sandbox random tokens, not equal to each other.
    assert pg.password != rd.password


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
async def test_provision_mongo_engine() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    info = await provisioner.provision("dev-mongo", ["mongo"])

    mongo = info.services["mongo"]
    assert mongo.host == "roboco-sandbox-mongo-dev-mongo"
    assert mongo.port == _MONGO_PORT
    assert mongo.user == "sandbox"
    assert mongo.database == "admin"
    run_call = next(c for c in runner.calls if c[0] == "run")
    assert "mongo:8" in run_call
    # MONGO_INITDB_ROOT_PASSWORD env is baked into the run.
    assert any(a.startswith("MONGO_INITDB_ROOT_PASSWORD=") for a in run_call)
    # /data/db tmpfs mount for the engine.
    assert "--tmpfs" in run_call
    assert run_call[run_call.index("--tmpfs") + 1] == "/data/db"


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


@pytest.mark.asyncio
async def test_provision_skips_pull_when_image_present() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.provision("dev-7", ["postgres"])

    assert any(c[0] == "image" and c[1] == "inspect" for c in runner.calls)
    assert not any(c[0] == "pull" for c in runner.calls)


@pytest.mark.asyncio
async def test_provision_pulls_when_image_absent() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    runner.image_present = False
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    await provisioner.provision("dev-8", ["postgres"])

    inspect = [c for c in runner.calls if c[0] == "image" and c[1] == "inspect"]
    pulls = [c for c in runner.calls if c[0] == "pull"]
    assert inspect and pulls
    assert pulls[0][-1] == "postgres:16-alpine"


@pytest.mark.asyncio
async def test_provision_pull_failure_raises() -> None:
    runner = _FakeRunner(run_rc=0, exec_rc=0)
    runner.image_present = False
    runner.pull_rc = 1
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    with pytest.raises(SandboxProvisionError, match="image pull failed"):
        await provisioner.provision("dev-9", ["postgres"])
    # `docker run` never reached — pull failed first.
    assert not any(c[0] == "run" for c in runner.calls)


@pytest.mark.asyncio
async def test_is_live_true_when_container_running() -> None:
    runner = _FakeRunner()

    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    assert await provisioner.is_live("dev-10", ["postgres", "redis"]) is True
    expected_inspect_calls = 2
    inspects = [c for c in runner.calls if c[0] == "inspect"]
    assert len(inspects) == expected_inspect_calls


@pytest.mark.asyncio
async def test_is_live_false_when_container_stopped() -> None:
    """rc 0 but State.Running == false — container exists but isn't running."""
    runner = _FakeRunner()
    runner.inspect_running = False
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    assert await provisioner.is_live("dev-11", ["postgres"]) is False


@pytest.mark.asyncio
async def test_is_live_false_when_container_missing() -> None:
    """Nonzero rc — `docker inspect` fails outright on a removed container."""
    runner = _FakeRunner()
    runner.inspect_rc = 1
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    assert await provisioner.is_live("dev-12", ["postgres"]) is False


@pytest.mark.asyncio
async def test_is_live_short_circuits_on_first_dead_service() -> None:
    """A dead first service skips checking the rest — no need to inspect
    every container once one is already known dead."""
    runner = _FakeRunner()
    runner.inspect_rc = 1
    provisioner = SandboxProvisioner(network=_NETWORK, runner=runner)

    assert await provisioner.is_live("dev-13", ["postgres", "redis"]) is False
    inspects = [c for c in runner.calls if c[0] == "inspect"]
    assert len(inspects) == 1
