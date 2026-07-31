"""roboco.runtime.compose_labels — self-id + cached compose-project discovery.

Every docker-run site splices ``compose_label_args(service)`` in; on a dev
machine / CI runner / the eval harness (never a compose-managed container)
discovery finds nothing and every call site is byte-for-byte unchanged —
that fallback is exercised implicitly by every OTHER spawn-cmd test in the
suite (none of them run inside a real compose stack), so this file focuses
on the helper's own mechanics: mountinfo parsing (including the real UGREEN
NAS btrfs shape), the HOSTNAME fallback, the docker-inspect path, the
definitive-vs-transient cache semantics, and the once-per-process cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime import compose_labels

if TYPE_CHECKING:
    from pathlib import Path

_FAKE_CONTAINER_ID = "a" * 64
# From a live UGREEN NAS container (DockerRootDir /volume1/@docker, a btrfs
# mount) — captured 2026-07-30. Root field (4th) carries the bind-mount
# source path; NOT /var/lib/docker.
_NAS_HOSTNAME_LINE = (
    "614 613 0:59 /@docker/containers/"
    "87ea7acf042857b338432c5a06563cdc4d5cef97d959efcb57da154b34e924ba/hostname "
    "/etc/hostname rw,relatime - btrfs /dev/bcache0 "
    "rw,ssd,space_cache=v2,subvolid=257\n"
)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts as if discovery never ran (module-level cache)."""
    monkeypatch.setattr(compose_labels, "_cache", compose_labels._DiscoveryCache())


@pytest.fixture(autouse=True)
def _clear_hostname_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic: the mountinfo-only tests must not accidentally pick up
    a real HOSTNAME from the runner's own environment via the fallback tier.
    Tests exercising the fallback set HOSTNAME explicitly."""
    monkeypatch.delenv("HOSTNAME", raising=False)


def _proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# _id_from_mountinfo / _id_from_hostname_env / _own_container_id
# ---------------------------------------------------------------------------


def test_own_container_id_none_when_mountinfo_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        compose_labels, "_MOUNTINFO_PATH", str(tmp_path / "does-not-exist")
    )
    assert compose_labels._own_container_id() is None


def test_own_container_id_none_when_no_docker_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real host's mountinfo (not a Docker container) has no matching line."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("1 2 0:1 / / rw,relatime - ext4 /dev/root rw\n")
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    assert compose_labels._own_container_id() is None


def test_own_container_id_parses_docker_bind_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The textbook /var/lib/docker data-root shape."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "614 613 253:1 /var/lib/docker/containers/"
        f"{_FAKE_CONTAINER_ID}/hostname /etc/hostname rw,relatime "
        "- ext4 /dev/root rw\n"
    )
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    assert compose_labels._own_container_id() == _FAKE_CONTAINER_ID


def test_own_container_id_parses_nas_btrfs_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real deploy target: UGREEN NAS, DockerRootDir /volume1/@docker, a
    btrfs subvolume mount — NOT /var/lib/docker. The id must still parse
    since the regex keys on the root-independent `/containers/<id>/<file>`
    suffix, not a specific data-root prefix."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(_NAS_HOSTNAME_LINE)
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    assert (
        compose_labels._own_container_id()
        == "87ea7acf042857b338432c5a06563cdc4d5cef97d959efcb57da154b34e924ba"
    )


def test_own_container_id_falls_back_to_hostname_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No usable mountinfo (e.g. a runtime that doesn't bind-mount per-container
    config) => fall back to Docker's default HOSTNAME-is-the-short-id."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("1 2 0:1 / / rw,relatime - ext4 /dev/root rw\n")
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    monkeypatch.setenv("HOSTNAME", "545315347e2f")
    assert compose_labels._own_container_id() == "545315347e2f"


def test_hostname_env_rejects_a_real_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-container HOSTNAME (a real machine name) must not be mistaken
    for a container id."""
    monkeypatch.setenv("HOSTNAME", "MacBook-Pro.local")
    assert compose_labels._id_from_hostname_env() is None


def test_mountinfo_takes_priority_over_hostname_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mountinfo wins when both are available — robust to a hostname override."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "614 613 253:1 /var/lib/docker/containers/"
        f"{_FAKE_CONTAINER_ID}/hostname /etc/hostname rw,relatime "
        "- ext4 /dev/root rw\n"
    )
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    monkeypatch.setenv("HOSTNAME", "545315347e2f")
    assert compose_labels._own_container_id() == _FAKE_CONTAINER_ID


# ---------------------------------------------------------------------------
# compose_label_args — end to end (discovery + cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_label_args_empty_outside_a_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No container id at all (dev machine / test env) => no labels, no docker call."""
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: None)
    with patch("asyncio.create_subprocess_exec") as create_exec:
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == []
    create_exec.assert_not_called()


@pytest.mark.asyncio
async def test_compose_label_args_present_when_project_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_proc(returncode=0, stdout=b"roboco-nas\n")),
    ):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == [
        "--label",
        "com.docker.compose.project=roboco-nas",
        "--label",
        "com.docker.compose.service=be-dev-1",
        "--label",
        "com.docker.compose.oneoff=False",
        "--label",
        f"com.docker.compose.config-hash={compose_labels._CONFIG_HASH_PLACEHOLDER}",
    ]


@pytest.mark.asyncio
async def test_hostname_fallback_id_reaches_docker_inspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: mountinfo empty, HOSTNAME=12-hex => docker inspect is
    called with that hostname value as the target container id."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("1 2 0:1 / / rw,relatime - ext4 /dev/root rw\n")
    monkeypatch.setattr(compose_labels, "_MOUNTINFO_PATH", str(mountinfo))
    monkeypatch.setenv("HOSTNAME", "545315347e2f")
    create_exec = AsyncMock(return_value=_proc(returncode=0, stdout=b"roboco-nas\n"))
    with patch("asyncio.create_subprocess_exec", create_exec):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert "com.docker.compose.project=roboco-nas" in result
    assert create_exec.call_args.args[-1] == "545315347e2f"


@pytest.mark.asyncio
async def test_compose_label_args_empty_when_inspect_fails_transiently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_proc(returncode=1, stderr=b"no such container\n")),
    ):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == []
    # Transient (nonzero inspect) — must NOT poison the cache forever.
    assert compose_labels._cache.discovered is False


@pytest.mark.asyncio
async def test_transient_inspect_failure_retries_on_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-call transient failure (e.g. daemon not ready yet) must not
    cache a permanent None — the very next spawn gets a fresh attempt."""
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_proc(returncode=1, stderr=b"daemon not ready\n")),
    ):
        first = await compose_labels.compose_label_args("be-dev-1")
    assert first == []

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_proc(returncode=0, stdout=b"roboco-nas\n")),
    ):
        second = await compose_labels.compose_label_args("be-dev-1")
    assert "com.docker.compose.project=roboco-nas" in second


@pytest.mark.asyncio
async def test_compose_label_args_empty_when_label_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real container not launched by compose has no project label — the
    `--format` template resolves to an empty string, not an error. This IS
    a definitive outcome (a completed inspect), so it gets cached."""
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=_proc(returncode=0, stdout=b"\n")),
    ):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == []
    assert compose_labels._cache.discovered is True
    assert compose_labels._cache.project is None


@pytest.mark.asyncio
async def test_compose_label_args_empty_when_docker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker CLI absent (FileNotFoundError from create_subprocess_exec) —
    transient, not cached."""
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError("docker not found")),
    ):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == []
    assert compose_labels._cache.discovered is False


@pytest.mark.asyncio
async def test_compose_label_args_empty_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=TimeoutError)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await compose_labels.compose_label_args("be-dev-1")
    assert result == []
    assert compose_labels._cache.discovered is False


@pytest.mark.asyncio
async def test_discovery_runs_docker_inspect_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second/third call for a different service reuses the cached project —
    the whole point of caching it process-wide."""
    monkeypatch.setattr(compose_labels, "_own_container_id", lambda: _FAKE_CONTAINER_ID)
    create_exec = AsyncMock(return_value=_proc(returncode=0, stdout=b"roboco-nas\n"))
    with patch("asyncio.create_subprocess_exec", create_exec):
        first = await compose_labels.compose_label_args("be-dev-1")
        second = await compose_labels.compose_label_args("fe-qa-1")

    assert create_exec.call_count == 1
    assert "com.docker.compose.service=be-dev-1" in first
    assert "com.docker.compose.service=fe-qa-1" in second
    # Both share the same resolved project.
    assert "com.docker.compose.project=roboco-nas" in first
    assert "com.docker.compose.project=roboco-nas" in second
