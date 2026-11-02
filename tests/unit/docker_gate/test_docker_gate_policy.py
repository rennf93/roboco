"""The Docker gate's stage-2 policy (trail #34): what a call may ask for.

``recorded/`` holds the requests RoboCo's own ``docker run``, ``docker exec``
and ``docker pull`` calls send (``scripts/record_docker_gate_bodies.py``):
every one must pass. Each other case changes one thing about one of them, or
asks for one thing RoboCo never does, and must be refused.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from roboco.models.sandbox import SANDBOX_ENGINE_FEATURES, SANDBOX_ENGINES

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_GATE = Path(__file__).parents[3] / "docker" / "docker-gate" / "gate.py"
_RECORDED = Path(__file__).parent / "recorded"
_ROOTS = (
    "/host/data",
    "/host/project/docs",
    "/host/home/.claude",
    "/host/home/.grok",
    "/host/home/.codex",
    "/host/home/.gemini",
    "/host/home/.kimi-code",
)
# What the compose files tag the orchestrator's own image, which the
# throwaway workspace container runs by id.
_ORCHESTRATOR_TAGS = (
    "roboco-orchestrator-blue:latest",
    "ghcr.io/rennf93/roboco-orchestrator:0.33.0",
)


@cache
def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docker_gate_policy", _GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _policy(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GATE_BIND_ROOTS", ":".join(_ROOTS))
    monkeypatch.setenv("GATE_REGISTRY", "ghcr.io/rennf93")
    monkeypatch.delenv("GATE_IMAGES", raising=False)
    monkeypatch.delenv("GATE_NETWORKS", raising=False)
    return _gate().Policy.from_env()


def _recorded() -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(_RECORDED.glob("*.json"))]


def _path(record: dict[str, Any]) -> str:
    target: str = record["target"]
    return target.split("?", 1)[0]


def _is_exec(record: dict[str, Any]) -> bool:
    return _path(record).endswith("/exec")


def _is_create(record: dict[str, Any]) -> bool:
    return _path(record).endswith("/containers/create")


def _agent_body() -> dict[str, Any]:
    record = next(r for r in _recorded() if r["source"] == "spawn:anthropic")
    return copy.deepcopy(record["body"])


def test_every_call_site_was_recorded() -> None:
    sources = {r["source"].split(":", 1)[0] for r in _recorded()}
    assert sources == {
        "spawn",
        "spawn-registry",
        "pull-registry",
        "secretary",
        "intake",
        "sandbox",
        "pull-sandbox",
        "sandbox-registry",
        "pull-sandbox-registry",
        "workspace-container",
        "exec",
    }


@pytest.mark.parametrize("record", _recorded(), ids=lambda r: r["source"])
def test_what_roboco_sends_passes(
    record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, policy = _gate(), _policy(monkeypatch)
    assert gate.decide(record["method"], record["target"], allow_build=False) is None
    body = record["body"]
    if _is_exec(record):
        assert gate.exec_problem(body) is None
        return
    if not _is_create(record):
        _, query = gate.api_path(record["target"])
        assert gate.pull_problem(query, policy) is None
        return
    assert gate.create_problem(body, policy) is None
    image = body["Image"]
    names = _ORCHESTRATOR_TAGS if image.startswith("sha256:") else [image]
    assert gate.image_allowed(names, policy)


@pytest.mark.parametrize(
    "record",
    [r for r in _recorded() if _is_create(r)],
    ids=lambda r: r["source"],
)
def test_recorded_binds_pass_on_disk(
    record: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same binds, with /host made real under tmp_path."""
    gate = _gate()
    roots = tuple(root.replace("/host", str(tmp_path), 1) for root in _ROOTS)
    binds = []
    for bind in record["body"]["HostConfig"].get("Binds") or []:
        local = bind.replace("/host", str(tmp_path), 1)
        source = Path(local.split(":", 1)[0])
        if source.suffix in {".md", ".json"}:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.touch()
        else:
            source.mkdir(parents=True, exist_ok=True)
        binds.append(local)
    monkeypatch.chdir(tmp_path)
    assert gate.binds_on_disk_problem(binds, roots) is None


def _set(path: str, value: object) -> Callable[[dict[str, Any]], None]:
    def mutate(body: dict[str, Any]) -> None:
        target = body
        *parents, last = path.split(".")
        for key in parents:
            target = target.setdefault(key, {})
        target[last] = value

    return mutate


def _add_bind(bind: str) -> Callable[[dict[str, Any]], None]:
    def mutate(body: dict[str, Any]) -> None:
        body["HostConfig"]["Binds"] = [*body["HostConfig"]["Binds"], bind]

    return mutate


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_set("HostConfig.Privileged", True), "privileged"),
        (_set("HostConfig.CapAdd", ["SYS_ADMIN"]), "CapAdd"),
        (_set("HostConfig.Devices", [{"PathOnHost": "/dev/sda"}]), "Devices"),
        (_set("HostConfig.DeviceRequests", [{"Count": -1}]), "DeviceRequests"),
        (_set("HostConfig.DeviceCgroupRules", ["b *:* rwm"]), "DeviceCgroupRules"),
        (_set("HostConfig.Mounts", [{"Type": "bind", "Source": "/"}]), "Mounts"),
        (_set("HostConfig.VolumesFrom", ["roboco-docker-gate-blue"]), "VolumesFrom"),
        (_set("HostConfig.GroupAdd", ["0"]), "GroupAdd"),
        (_set("HostConfig.Cgroup", "host"), "Cgroup"),
        (_set("HostConfig.VolumeDriver", "local-persist"), "VolumeDriver"),
        (_set("HostConfig.Runtime", "runsc"), "Runtime"),
        (_set("HostConfig.Sysctls", {"kernel.core_pattern": "|/x"}), "Sysctls"),
        (_set("HostConfig.SecurityOpt", ["seccomp=unconfined"]), "SecurityOpt"),
        (_set("HostConfig.SecurityOpt", ["apparmor=unconfined"]), "SecurityOpt"),
        (_set("HostConfig.SecurityOpt", ["label=disable"]), "SecurityOpt"),
        # --security-opt systempaths=unconfined: the CLI sends both lists empty.
        (_set("HostConfig.MaskedPaths", []), "MaskedPaths"),
        (_set("HostConfig.ReadonlyPaths", []), "ReadonlyPaths"),
        (_set("HostConfig.RestartPolicy", {"Name": "always"}), "restart"),
        (_set("HostConfig.NetworkMode", "host"), "network"),
        (_set("HostConfig.NetworkMode", "container:roboco-postgres"), "network"),
        (_set("HostConfig.NetworkMode", "roboco_data"), "network"),
        (_set("NetworkingConfig.EndpointsConfig", {"roboco_data": {}}), "networks"),
        (_set("HostConfig.PidMode", "host"), "PidMode"),
        (_set("HostConfig.PidMode", "container:roboco-postgres"), "PidMode"),
        (_set("HostConfig.IpcMode", "host"), "IpcMode"),
        (_set("HostConfig.UTSMode", "host"), "UTSMode"),
        (_set("HostConfig.UsernsMode", "host"), "UsernsMode"),
        (_set("HostConfig.CgroupnsMode", "host"), "CgroupnsMode"),
        (_set("HostConfig.ExtraHosts", ["evil:10.0.0.1"]), "ExtraHosts"),
        (_set("User", "root"), "root"),
        (_set("User", "0"), "root"),
        (_set("User", "0:0"), "root"),
        (_set("User", "root:agent"), "root"),
        (_add_bind("/:/host"), "below /"),
        (_add_bind("/var/run/docker.sock:/var/run/docker.sock"), "Docker socket"),
        (_add_bind("/run/docker.sock:/s"), "Docker socket"),
        (_add_bind("/var/run:/r"), "Docker socket"),
        (_add_bind("/etc:/etc"), "outside the bind roots"),
        (_add_bind("/host/database:/d"), "outside the bind roots"),
        (_add_bind("/host/data/../../etc:/e"), "normalized"),
        (_add_bind("/host/data/:/d"), "normalized"),
        (_add_bind("roboco-volume:/v"), "volume"),
        (_add_bind("/host/data:/d:ro:extra"), "source:target"),
    ],
)
def test_one_thing_more_than_roboco_asks_for_is_refused(
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _agent_body()
    mutate(body)
    problem = _gate().create_problem(body, _policy(monkeypatch))
    assert problem is not None
    assert expected in problem


@pytest.mark.parametrize("body", [None, [], "x", {"HostConfig": []}])
def test_a_create_body_that_is_not_an_object_is_refused(
    body: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _gate().create_problem(body, _policy(monkeypatch)) is not None


@pytest.mark.parametrize(
    "image",
    [
        "evil/roboco-agent-dev-be",
        "docker.io/evil/roboco-agent-dev-be",
        "postgres:latest",
        "ubuntu",
        "ghcr.io/someone-else/roboco-agent-dev-be:1",
    ],
)
def test_an_image_roboco_does_not_run_is_not_allowed(
    image: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not _gate().image_allowed([image], _policy(monkeypatch))


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/library/postgres:16-alpine",
        "index.docker.io/library/mongo:8",
        "docker.io/redis/redis-stack-server:latest",
    ],
)
def test_docker_hub_long_names_are_the_same_images(
    image: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _gate().image_allowed([image], _policy(monkeypatch))


def test_without_a_registry_only_local_images_are_allowed() -> None:
    policy = _gate().Policy()
    assert _gate().image_allowed(["roboco-agent-dev-be"], policy)
    assert not _gate().image_allowed(["ghcr.io/rennf93/roboco-agent-dev-be:1"], policy)
    assert not _gate().image_allowed(
        ["ghcr.io/rennf93/roboco-sandbox-pg:0.33.0"], policy
    )


def test_a_registry_allows_robocos_sandbox_image_from_it_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy(monkeypatch)
    assert _gate().image_allowed(["ghcr.io/rennf93/roboco-sandbox-pg:0.33.0"], policy)
    assert not _gate().image_allowed(["ghcr.io/other/roboco-sandbox-pg:0.33.0"], policy)
    assert not _gate().image_allowed(["ghcr.io/rennf93/roboco-sandbox-x:1"], policy)
    # Upstream sandbox images are pulled by their own names, never the registry's.
    assert not _gate().image_allowed(["ghcr.io/rennf93/postgres:16-alpine"], policy)


@pytest.mark.parametrize(
    "body",
    [
        {"Cmd": ["sh"], "Privileged": True},
        {"Cmd": ["sh"], "User": "root"},
        {"Cmd": ["sh"], "User": "0"},
        {"Cmd": ["sh"], "User": "postgres"},
        ["sh"],
    ],
)
def test_an_exec_asking_for_more_is_refused(body: object) -> None:
    assert _gate().exec_problem(body) is not None


@pytest.mark.parametrize(
    ("query", "allowed"),
    [
        ({"fromImage": ["postgres"], "tag": ["16-alpine"]}, True),
        ({"fromImage": ["redis/redis-stack-server"], "tag": ["latest"]}, True),
        (
            {"fromImage": ["ghcr.io/rennf93/roboco-agent-codex"], "tag": ["0.33.0"]},
            True,
        ),
        ({"fromImage": ["ubuntu"], "tag": ["latest"]}, False),
        ({"fromImage": ["evil.io/roboco-agent-x"], "tag": ["1"]}, False),
        # An image import from a tarball or URL, not a pull.
        ({"fromSrc": ["-"], "repo": ["roboco-agent-x"]}, False),
        ({"fromImage": ["postgres", "ubuntu"]}, False),
    ],
)
def test_a_pull_fetches_only_images_roboco_runs(
    query: dict[str, list[str]], allowed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = _gate().pull_problem(query, _policy(monkeypatch))
    assert (problem is None) is allowed


@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        ("/v1.51/images/prune?filters=%7B%22dangling%22%3A%5B%22true%22%5D%7D", True),
        (
            "/v1.51/images/prune?filters=%7B%22dangling%22%3A%7B%22true%22%3Atrue%7D%7D",
            True,
        ),
        ("/v1.51/images/prune", False),
        ("/v1.51/images/prune?filters=%7B%22dangling%22%3A%5B%22false%22%5D%7D", False),
        ("/v1.51/images/prune?filters=%7B%22label%22%3A%5B%22x%22%5D%7D", False),
        ("/v1.51/images/prune?filters=not-json", False),
    ],
)
def test_image_prune_is_dangling_only(target: str, allowed: bool) -> None:
    problem = _gate().decide("POST", target, allow_build=False)
    assert (problem is None) is allowed


@pytest.mark.parametrize(
    "root", ["/", "/var", "/var/run", "/run", "relative/path", "/a/../b", "/a/"]
)
def test_a_bind_root_that_could_expose_the_host_stops_the_gate(
    root: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATE_BIND_ROOTS", f"/host/data:{root}")
    with pytest.raises(ValueError, match="GATE_BIND_ROOTS"):
        _gate().Policy.from_env()


def test_no_bind_roots_means_no_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_BIND_ROOTS", raising=False)
    policy = _gate().Policy.from_env()
    assert policy.bind_roots == ()
    assert "outside" in _gate().create_problem(_agent_body(), policy)


def test_a_plain_path_passes_on_disk(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f.json").touch()
    gate = _gate()
    assert gate.symlink_problem(str(tmp_path / "a" / "b"), str(tmp_path)) is None
    assert gate.symlink_problem(str(tmp_path / "a/b/f.json"), str(tmp_path)) is None
    assert gate.symlink_problem(str(tmp_path), str(tmp_path)) is None


@pytest.mark.parametrize(
    ("make", "source"),
    [
        # A directory on the way is a symlink (to / here).
        (lambda root: (root / "a").symlink_to("/"), "a/etc"),
        # The source itself is a symlink, to a directory or a file.
        (lambda root: (root / "a").symlink_to("/etc"), "a"),
        (lambda root: (root / "f").symlink_to("/etc/hostname"), "f"),
        # A symlink to somewhere inside the root is refused all the same.
        (
            lambda root: (
                (root / "real").mkdir(),
                (root / "a").symlink_to(root / "real"),
            ),
            "a",
        ),
        # Missing, and a file where a directory should be.
        (lambda _root: None, "missing/x"),
        (lambda root: (root / "f").touch(), "f/x"),
    ],
)
def test_a_bind_that_is_not_a_plain_path_on_disk_is_refused(
    make: Callable[[Path], object], source: str, tmp_path: Path
) -> None:
    make(tmp_path)
    problem = _gate().symlink_problem(str(tmp_path / source), str(tmp_path))
    assert problem is not None


def test_the_on_disk_check_covers_every_bind(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("/etc")
    binds = [f"{tmp_path}/real:/a", f"{tmp_path}/link:/b:ro"]
    problem = _gate().binds_on_disk_problem(binds, (str(tmp_path),))
    assert problem is not None
    assert "symlink" in problem


@pytest.mark.parametrize(
    ("info", "mine"),
    [
        ({"Name": "/roboco-agent-be-dev-1", "Config": {"Labels": {}}}, True),
        ({"Name": "/roboco-wsx-0123", "Config": {"Labels": None}}, True),
        ({"Name": "/x", "Config": {"Labels": {"roboco.sandbox": "1"}}}, True),
        ({"Name": "/roboco-postgres", "Config": {"Labels": {}}}, False),
        ({"Name": "/roboco-docker-gate-blue", "Config": {}}, False),
        ({}, False),
    ],
)
def test_a_container_is_robocos_by_name_or_label(
    info: dict[str, Any], mine: bool
) -> None:
    assert _gate().container_is_robocos(info) is mine


@pytest.mark.parametrize(
    ("user", "allowed"),
    [("agent", True), ("1000:1000", True), ("", False), ("0", False), ("root", False)],
)
def test_a_start_refuses_root_with_binds(
    user: str, allowed: bool, tmp_path: Path
) -> None:
    (tmp_path / "data").mkdir()
    info = {
        "Config": {"User": user},
        "HostConfig": {"Binds": [f"{tmp_path}/data:/data"]},
    }
    policy = _gate().Policy(bind_roots=(str(tmp_path),))
    assert (_gate().start_problem(info, policy) is None) is allowed


def test_a_start_without_binds_may_run_as_root() -> None:
    info = {"Config": {"User": ""}, "HostConfig": {"Binds": None}}
    assert _gate().start_problem(info, _gate().Policy()) is None


def test_the_sandbox_images_are_the_engines_images() -> None:
    images = set()
    for name, engine in SANDBOX_ENGINES.items():
        images.add(engine.image_for([]))
        for feature in SANDBOX_ENGINE_FEATURES.get(name, ()):
            images.add(engine.image_for([feature]))
    assert images == set(_gate().SANDBOX_IMAGES)
