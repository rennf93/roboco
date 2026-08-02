"""Discover the orchestrator's own Docker Compose project, once per process.

Every container the orchestrator creates as a sibling of itself — agent
spawns (including the persistent intake/secretary chats) and sandbox
DB/Redis/Mongo sidecars — should carry the same
``com.docker.compose.project``/``com.docker.compose.service`` labels the
compose stack stamps on its own service containers. That buys two things: a
Docker UI (e.g. UGOS) groups every spawned container under the same project
as the stack, and `docker compose down --remove-orphans` sweeps them away
WITH the stack — the CEO wants agents to die with their orchestrator, since
an agent whose orchestrator is gone can't do anything.

**Full compose-lifecycle semantics (intended, CEO-decided).** These labeled
sibling containers are NOT compose-file services — they exist only via
`docker run`, invisible to the compose file itself. That gives them exactly
the lifecycle a plain `--label` buys and no more: a bare `docker compose
stop` stops them (label-matched, like any other project container) and a
bare `docker compose restart` restarts them; `docker compose down` removes
them — verified empirically both with and without `--remove-orphans` (see
below for why `--remove-orphans` specifically needs a 4th label to even see
them). Critically, `docker compose up -d` does NOT resurrect them — `up`
only reconciles containers for services declared in the compose file, and a
labeled sidecar declares no service. This is deliberate, not a gap: an agent
without its orchestrator can't do anything, so it should never survive a
`down`, and after a redeploy (`up -d` bringing the orchestrator back) the
orchestrator's own startup respawns its agents itself — nothing about
resurrecting them is compose's job.

Verified empirically (live `docker compose down --remove-orphans`, compose
v5.3.1): a sidecar needs FOUR labels, not the three one would guess from
project/service/oneoff alone. Compose's own container listing
(``getDefaultFilters`` in ``docker/compose`` ``pkg/compose/containers.go``)
unconditionally adds a ``label=com.docker.compose.config-hash`` filter to
the Docker API query it runs BEFORE the orphan predicate ever sees a
container — a sidecar missing that label key is invisible to `down` at the
API level, orphan or not, no matter how correctly project/service/oneoff are
set. Its value is never read (only the label's presence gates the API-level
list), so a fixed placeholder is correct.

Self-identification reads this container's own ``/proc/self/mountinfo``
first, rather than its hostname. Docker's default (no ``hostname:`` in
compose — true today for the orchestrator service in both docker-compose.yml
and docker-compose.registry.yml) sets a container's hostname to its own
short id, which would work too — but silently breaks the moment a future
compose edit adds an explicit ``hostname:`` for the orchestrator service,
with nothing erroring anywhere to catch it. Docker bind-mounts /etc/hostname,
/etc/hosts, and resolv.conf from a per-container config dir into every
container regardless of any hostname override, so the real id is recoverable
from our own mountinfo independent of hostname entirely — and independent of
the docker data-root path too: a real UGREEN NAS container's mountinfo reads
``/@docker/containers/<id>/hostname`` (data-root ``/volume1/@docker``, a
btrfs mount), not the textbook ``/var/lib/docker/containers/<id>/hostname``,
so the pattern below keys on the root-independent ``/containers/<id>/<file>``
suffix rather than assuming a specific data-root prefix. The HOSTNAME env var
is a second-tier fallback (Docker's default-hostname-is-the-short-id
behaviour, verified true for the orchestrator service today) for a runtime
that doesn't bind-mount those per-container files the way Docker does.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_MOUNTINFO_PATH = "/proc/self/mountinfo"
# Root-independent: matches both /var/lib/docker/containers/<id>/hostname and
# a NAS's /@docker/containers/<id>/hostname (or any other docker data-root) —
# see module docstring.
_CONTAINER_ID_RE = re.compile(
    r"/containers/([0-9a-f]{64})/(?:hostname|hosts|resolv\.conf)"
)
_HOSTNAME_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_DOCKER_INSPECT_TIMEOUT_SECONDS = 5.0

# Any value works (compose never reads it — see module docstring); it only
# has to be a present label key.
_CONFIG_HASH_PLACEHOLDER = "roboco-sidecar"


@dataclass
class _DiscoveryCache:
    """Mutated in place (never rebound) so `_discover` needs no `global`.

    ``discovered`` only flips True on a DEFINITIVE outcome (a resolved
    project, or a confirmed "not under compose") — a transient failure
    (docker missing, a timeout, a nonzero inspect) leaves it False so the
    next spawn retries instead of caching a false negative forever.
    """

    discovered: bool = False
    project: str | None = None


_lock = asyncio.Lock()
_cache = _DiscoveryCache()


def _id_from_mountinfo() -> str | None:
    """This process's own container id, parsed from /proc/self/mountinfo.

    None outside a container (file absent/unreadable) or under a container
    runtime that doesn't bind-mount per-container config the way Docker does.
    """
    try:
        with Path(_MOUNTINFO_PATH).open(encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    match = _CONTAINER_ID_RE.search(content)
    return match.group(1) if match else None


def _id_from_hostname_env() -> str | None:
    """Fallback: Docker's default (no `hostname:` override) sets HOSTNAME to
    the container's own short id; `docker inspect` accepts a short-id prefix.
    """
    hostname = os.environ.get("HOSTNAME", "")
    return hostname if _HOSTNAME_ID_RE.match(hostname) else None


def _own_container_id() -> str | None:
    """Mountinfo first (robust to a hostname override), HOSTNAME second."""
    return _id_from_mountinfo() or _id_from_hostname_env()


async def _inspect_compose_project(container_id: str) -> tuple[bool, str | None]:
    """`docker inspect` our own compose-project label.

    Returns ``(definitive, project)``. ``definitive`` is False for a
    transient failure (docker CLI missing, a timeout, a nonzero inspect exit
    — daemon not ready yet, etc.) that deserves a retry on the next call, and
    True for a completed inspect regardless of whether the label was present
    (a container genuinely not started by compose has no label — that is a
    real, stable answer, not a failure).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_DOCKER_INSPECT_TIMEOUT_SECONDS
        )
    except (OSError, TimeoutError):
        return False, None
    if proc.returncode != 0:
        return False, None
    return True, (stdout.decode().strip() or None)


async def _discover() -> str | None:
    """Resolve + cache the orchestrator's own compose project, once.

    Lock-guarded so two concurrent first-callers (e.g. two agent spawns
    racing at startup) can't both run `docker inspect`; every call after a
    DEFINITIVE resolution is a plain attribute read once the lock is free.
    """
    async with _lock:
        if _cache.discovered:
            return _cache.project
        container_id = _own_container_id()
        if not container_id:
            _cache.project = None
            _cache.discovered = True
            logger.info("compose project discovery: no container id found")
            return None
        definitive, project = await _inspect_compose_project(container_id)
        if not definitive:
            logger.warning(
                "compose project discovery failed transiently; will retry",
                container_id=container_id,
            )
            return None
        _cache.project = project
        _cache.discovered = True
        if project:
            logger.info("compose project resolved", project=project)
        else:
            logger.info("container is not part of a compose project")
        return project


async def compose_label_args(service: str) -> list[str]:
    """Ready-to-splice ``--label`` argv for a sibling container of ``service``.

    Empty when the orchestrator isn't running under compose, or discovery
    hasn't yet succeeded — every call site's docker run cmd is then
    byte-for-byte unchanged, matching current behaviour on dev machines /
    tests / the eval harness (and self-heals on the next spawn after a
    transient discovery failure).
    """
    project = await _discover()
    if not project:
        return []
    return [
        "--label",
        f"com.docker.compose.project={project}",
        "--label",
        f"com.docker.compose.service={service}",
        "--label",
        "com.docker.compose.oneoff=False",
        "--label",
        f"com.docker.compose.config-hash={_CONFIG_HASH_PLACEHOLDER}",
    ]
