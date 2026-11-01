"""scripts/deploy-env.sh: the host-specific .env values the NAS deploy writes.

2026-10-28 the guard logged "host-proxied tailnet chain detected but no
trusted chain peer configured ... set ROBOCO_GUARD_TRUSTED_CHAIN_PEERS to your
docker bridge gateway (172.23.0.1)": the CEO's tailnet panel resolved to the
bridge gateway, so all of it shared one client identity. The gateway belongs
to the docker host, so the deploy reads it from docker instead of anyone
committing it. These run the real shell helpers against a fake ``docker``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_HELPERS = Path(__file__).parents[2] / "scripts" / "deploy-env.sh"
_GATEWAY = "172.23.0.1"
_CHAIN_PEERS = "ROBOCO_GUARD_TRUSTED_CHAIN_PEERS"


def _fake_docker(bin_dir: Path, inspect_output: str | None) -> None:
    """A ``docker`` that answers ``network inspect`` with ``inspect_output``,
    or fails like a missing network when it is None."""
    script = (
        "#!/bin/sh\nexit 1\n"
        if inspect_output is None
        else f"#!/bin/sh\nprintf '%s' '{inspect_output}'\n"
    )
    docker = bin_dir / "docker"
    docker.write_text(script)
    docker.chmod(0o755)


def _run(stack_dir: Path, call: str, extra_env: dict[str, str] | None = None) -> str:
    env = {
        "PATH": f"{stack_dir / 'bin'}:{os.environ['PATH']}",
        **(extra_env or {}),
    }
    done = subprocess.run(
        ["bash", "-c", f"set -euo pipefail; source {_HELPERS}; {call}"],
        cwd=stack_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def stack_dir(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    return tmp_path


def _env_file(stack_dir: Path) -> str:
    path = stack_dir / ".env"
    return path.read_text() if path.exists() else ""


def test_chain_peer_is_the_network_gateway(stack_dir: Path) -> None:
    _fake_docker(stack_dir / "bin", f"{_GATEWAY} ")
    (stack_dir / ".env").write_text("ROBOCO_REDIS_PASSWORD=x")
    _run(stack_dir, "ensure_env_chain_peer roboco_default")
    assert (
        _env_file(stack_dir) == f"ROBOCO_REDIS_PASSWORD=x\n{_CHAIN_PEERS}={_GATEWAY}\n"
    )


def test_chain_peer_skips_a_non_ipv4_gateway(stack_dir: Path) -> None:
    _fake_docker(stack_dir / "bin", f"fd00:23::1 {_GATEWAY} ")
    _run(stack_dir, "ensure_env_chain_peer roboco_default")
    assert _env_file(stack_dir) == f"{_CHAIN_PEERS}={_GATEWAY}\n"


def test_chain_peer_waits_for_the_network_to_exist(stack_dir: Path) -> None:
    _fake_docker(stack_dir / "bin", None)
    out = _run(stack_dir, "ensure_env_chain_peer roboco_default")
    assert _env_file(stack_dir) == ""
    assert "derived on the next deploy" in out


def test_chain_peer_never_replaces_a_declared_value(stack_dir: Path) -> None:
    _fake_docker(stack_dir / "bin", f"{_GATEWAY} ")
    (stack_dir / ".env").write_text(f"{_CHAIN_PEERS}=\n")
    _run(stack_dir, "ensure_env_chain_peer roboco_default")
    _run(
        stack_dir,
        "ensure_env_chain_peer roboco_default",
        extra_env={_CHAIN_PEERS: "172.18.0.1"},
    )
    assert _env_file(stack_dir) == f"{_CHAIN_PEERS}=\n"


def test_secret_is_generated_once(stack_dir: Path) -> None:
    _run(stack_dir, "ensure_env_secret ROBOCO_REDIS_PASSWORD")
    first = _env_file(stack_dir)
    _run(stack_dir, "ensure_env_secret ROBOCO_REDIS_PASSWORD")
    assert first.startswith("ROBOCO_REDIS_PASSWORD=")
    assert _env_file(stack_dir) == first


def test_deploy_script_derives_the_chain_peer_from_nginx_network() -> None:
    script = (_HELPERS.parent / "deploy-nas.sh").read_text()
    assert 'source "$STACK_DIR/scripts/deploy-env.sh"' in script
    assert "ensure_env_chain_peer roboco_default" in script
