"""``_GitReleaseOps`` subprocesses (git, ``make quality``, ``gh release create``,
the release-clone ``git clone``) are wrapped in ``asyncio.wait_for`` with a
kill-on-timeout fail-close so a hung child cannot block the release loop.

These tests hang the subprocess (a never-resolving ``communicate``) and patch
the timeout constants tiny so a deterministic fail-close is asserted in well
under a second — never relying on real wall-clock timing of the defaults.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from roboco.services.release_executor import (
    _CLONE_TIMEOUT_SECONDS,
    _GIT_OP_TIMEOUT_SECONDS,
    _PUBLISH_TIMEOUT_SECONDS,
    _RELEASE_GATE_TIMEOUT_SECONDS,
    _TIMEOUT_RC,
    _GitReleaseOps,
    _run,
)

# Floors encoding the logical-regression guard: a deadline below these would
# silently abort a legitimate slow release. Named (not magic) for ruff PLR2004.
_MIN_GATE_TIMEOUT = 1800  # full make quality suite — ruff/mypy/pytest
_MIN_GIT_OP_TIMEOUT = 300  # network push / ls-remote
_MIN_CLONE_TIMEOUT = 600  # full clone on a slow link
_MIN_PUBLISH_TIMEOUT = 120  # gh release create


# ---------------------------------------------------------------------------
# Fakes: a hanging subprocess (never-resolving communicate) and a done one.
# ---------------------------------------------------------------------------


class _HangingProc:
    """Subprocess whose ``communicate()`` never resolves — a hung git/make/gh.

    Records ``kill()`` so a test can assert the child was reaped, not leaked.
    """

    def __init__(self) -> None:
        self.killed = False
        self._never: asyncio.Future[None] = asyncio.Future()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._never  # wait_for cancels this on timeout -> TimeoutError
        return (b"", b"")

    def kill(self) -> None:
        self.killed = True


class _DoneProc:
    """Subprocess that completes immediately with a fixed rc + stdout."""

    def __init__(self, returncode: int, out: bytes) -> None:
        self.returncode = returncode
        self._out = out
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return (self._out, b"")

    def kill(self) -> None:
        self.killed = True


def _exec_returning(proc: object) -> object:
    async def _exec(*_args: object, **_kwargs: object) -> object:
        return proc

    return _exec


def _ops() -> _GitReleaseOps:
    """A ``_GitReleaseOps`` built without a DB session — these methods only use
    ``self._root`` / ``self._default_branch``, never the session."""
    ops = _GitReleaseOps.__new__(_GitReleaseOps)
    ops._slug = "roboco"
    ops._default_branch = "master"
    ops._root = Path("/tmp/roboco-release-f078")
    ops._auth_url = "https://x@github.com/o/roboco"
    ops._ci_workflow = None
    return ops


# ---------------------------------------------------------------------------
# Constant shape — generous floors so a legitimate slow op isn't wrongly killed.
# ---------------------------------------------------------------------------


def test_timeouts_are_named_module_constants() -> None:
    """Each deadline is a module-level constant (ruff PLR2004), not a magic
    number inline."""
    for c in (
        _GIT_OP_TIMEOUT_SECONDS,
        _RELEASE_GATE_TIMEOUT_SECONDS,
        _PUBLISH_TIMEOUT_SECONDS,
        _CLONE_TIMEOUT_SECONDS,
    ):
        assert isinstance(c, int | float)
        assert c > 0


def test_gate_timeout_is_generous_enough_for_a_full_make_quality() -> None:
    """``make quality`` runs the full ruff/mypy/pytest suite — a legitimate run
    can take many minutes. The deadline must be large enough that a healthy
    release is never aborted; 30 min is consistent with the ~40 min CI poll
    ceiling. This guards the logical regression: a too-short gate timeout would
    silently fail-closed a release that would have succeeded."""
    assert _RELEASE_GATE_TIMEOUT_SECONDS >= _MIN_GATE_TIMEOUT


def test_network_op_timeouts_are_generous() -> None:
    """Network git ops (push, ls-remote) and a full clone can legitimately take
    minutes on a slow link; the deadlines must not wrongly abort them."""
    assert _GIT_OP_TIMEOUT_SECONDS >= _MIN_GIT_OP_TIMEOUT
    assert _CLONE_TIMEOUT_SECONDS >= _MIN_CLONE_TIMEOUT
    assert _PUBLISH_TIMEOUT_SECONDS >= _MIN_PUBLISH_TIMEOUT


# ---------------------------------------------------------------------------
# Hung subprocess → fail-closed within the (tiny, patched) deadline.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_op_times_out_and_kills_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung git op returns rc 124 (fail-closed) and kills the child proc — it
    must not hang the release loop."""
    monkeypatch.setattr(
        "roboco.services.release_executor._GIT_OP_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingProc()
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    ops = _ops()
    rc, out = await asyncio.wait_for(ops._git("rev-parse", "HEAD"), timeout=2.0)
    assert rc == _TIMEOUT_RC
    assert "timed out" in out
    assert proc.killed


@pytest.mark.asyncio
async def test_run_gate_times_out_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung ``make quality`` fails closed: ``run_gate`` returns False (the
    release aborts before commit), and the child is killed — not wedged."""
    monkeypatch.setattr(
        "roboco.services.release_executor._RELEASE_GATE_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingProc()
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    ops = _ops()
    passed = await asyncio.wait_for(ops.run_gate(), timeout=2.0)
    assert passed is False
    assert proc.killed


@pytest.mark.asyncio
async def test_publish_release_times_out_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung ``gh release create`` raises RuntimeError (fail-closed — never
    reports a bogus published URL) and kills the child."""
    monkeypatch.setattr(
        "roboco.services.release_executor._PUBLISH_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingProc()
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    ops = _ops()
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(ops.publish_release("1.0.0", "notes"), timeout=2.0)
    assert proc.killed


@pytest.mark.asyncio
async def test_clone_run_times_out_and_kills_proc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung ``git clone`` (the release-clone prep) returns rc 124 and kills
    the child — the caller raises, fail-closed, instead of hanging."""
    monkeypatch.setattr("roboco.services.release_executor._CLONE_TIMEOUT_SECONDS", 0.05)
    proc = _HangingProc()
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    rc, out = await asyncio.wait_for(
        _run(["git", "clone", "https://x@github.com/o/roboco", "/tmp/x"]),
        timeout=2.0,
    )
    assert rc == _TIMEOUT_RC
    assert "timed out" in out
    assert proc.killed


# ---------------------------------------------------------------------------
# Regression: the happy path still returns the real rc + decoded output.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_op_green_path_returns_real_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeout wrap must not break the happy path: a completing proc
    returns its real rc + decoded stdout, and is NOT killed."""
    proc = _DoneProc(0, b"deadbeef\n")
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    ops = _ops()
    rc, out = await asyncio.wait_for(ops._git("rev-parse", "HEAD"), timeout=2.0)
    assert rc == 0
    assert out == "deadbeef\n"
    assert not proc.killed


@pytest.mark.asyncio
async def test_run_gate_green_path_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green ``make quality`` (rc 0) returns True — the timeout wrap doesn't
    turn a passing gate into a failure."""
    proc = _DoneProc(0, b"all good\n")
    monkeypatch.setattr(
        "roboco.services.release_executor.asyncio.create_subprocess_exec",
        _exec_returning(proc),
    )
    ops = _ops()
    passed = await asyncio.wait_for(ops.run_gate(), timeout=2.0)
    assert passed is True
    assert not proc.killed
