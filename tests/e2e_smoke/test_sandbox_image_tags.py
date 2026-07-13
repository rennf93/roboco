"""Regression guard for the 2026-07-08 ``mongo:8-alpine`` ghost-tag bug.

``roboco/models/sandbox.py`` pinned ``_MongoEngine.image = "mongo:8-alpine"``,
a tag that has never existed on Docker Hub (MongoDB ships no Alpine variant).
Every unit test mocks the docker CLI, so none of them ever touch a real
registry and none caught it — the bug only surfaces the moment a real
``docker run`` pulls the image. This test queries the Docker Hub registry API
for every upstream image a sandbox engine may run (the bare ``image`` AND any
``kitchen_sink_image`` used when extensions/modules are requested) and fails if
the tag does not actually exist — the check that would have caught it.

Locally-built images (the ``roboco-sandbox-pg`` kitchen-sink, built by the
``sandbox-pg-image`` compose service) are skipped: they aren't on Docker Hub.
Namespaced upstream images (``redis/redis-stack-server``) use the namespaced
registry endpoint; bare library images (``postgres``, ``redis``, ``mongo``)
use the ``library/`` endpoint.

Network-dependent by design; skips cleanly when the registry is unreachable
rather than failing (mirrors ``test_background_engines.py``'s local-Redis
reachability skip).
"""

from __future__ import annotations

import httpx
import pytest
from roboco.models.sandbox import SANDBOX_ENGINES

# Docker Hub registry endpoints — library images live under ``library/``;
# namespaced images (e.g. ``redis/redis-stack-server``) live under their ns.
_REGISTRY_URL_LIBRARY = (
    "https://registry.hub.docker.com/v2/repositories/library/{name}/tags/{tag}"
)
_REGISTRY_URL_NS = "https://registry.hub.docker.com/v2/repositories/{name}/tags/{tag}"
_TIMEOUT_SECONDS = 10.0
_HTTP_OK = 200


def _split_image(image: str) -> tuple[str, str]:
    name, _, tag = image.partition(":")
    return name, tag or "latest"


def _engine_images() -> list[tuple[str, str]]:
    """(engine_name, image) pairs for every upstream image an engine may run."""
    pairs: list[tuple[str, str]] = []
    for name, engine in sorted(SANDBOX_ENGINES.items()):
        pairs.append((name, engine.image))
        kitchen = getattr(engine, "kitchen_sink_image", None)
        if kitchen:
            pairs.append((name, kitchen))
    return pairs


@pytest.mark.parametrize(
    "engine_name,image",
    _engine_images(),
    ids=[f"{n}={img}" for n, img in _engine_images()],
)
def test_sandbox_engine_image_tag_exists_on_docker_hub(
    engine_name: str, image: str
) -> None:
    name, tag = _split_image(image)
    # Locally-built project images (no registry namespace, roboco- prefix) are
    # built by a compose service, not pulled — skip the Docker Hub check.
    if "/" not in name and name.startswith("roboco-"):
        pytest.skip(f"{image!r} is a locally-built image (not on Docker Hub)")
    url = (_REGISTRY_URL_NS if "/" in name else _REGISTRY_URL_LIBRARY).format(
        name=name, tag=tag
    )

    try:
        resp = httpx.get(url, timeout=_TIMEOUT_SECONDS)
    except httpx.TransportError:
        pytest.skip(f"Docker Hub registry unreachable, cannot verify {image!r}")

    assert resp.status_code == _HTTP_OK, (
        f"{engine_name}: pinned image {image!r} not found on Docker Hub "
        f"({name}, tag {tag!r}, status {resp.status_code}) — {url}"
    )
