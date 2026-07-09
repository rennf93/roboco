"""Regression guard for the 2026-07-08 ``mongo:8-alpine`` ghost-tag bug.

``roboco/models/sandbox.py`` pinned ``_MongoEngine.image = "mongo:8-alpine"``,
a tag that has never existed on Docker Hub (MongoDB ships no Alpine variant).
Every unit test mocks the docker CLI, so none of them ever touch a real
registry and none caught it — the bug only surfaces the moment a real
``docker run`` pulls the image. This test queries the Docker Hub registry API
for every ``SANDBOX_ENGINES`` entry's pinned ``image:tag`` and fails if the
tag does not actually exist, which is the check that would have caught it.

Network-dependent by design; skips cleanly when the registry is unreachable
rather than failing (mirrors ``test_background_engines.py``'s local-Redis
reachability skip).
"""

from __future__ import annotations

import httpx
import pytest
from roboco.models.sandbox import SANDBOX_ENGINES

_REGISTRY_URL = (
    "https://registry.hub.docker.com/v2/repositories/library/{name}/tags/{tag}"
)
_TIMEOUT_SECONDS = 10.0
_HTTP_OK = 200


def _split_image(image: str) -> tuple[str, str]:
    name, _, tag = image.partition(":")
    return name, tag or "latest"


@pytest.mark.parametrize("engine_name", sorted(SANDBOX_ENGINES))
def test_sandbox_engine_image_tag_exists_on_docker_hub(engine_name: str) -> None:
    image = SANDBOX_ENGINES[engine_name].image
    name, tag = _split_image(image)
    url = _REGISTRY_URL.format(name=name, tag=tag)

    try:
        resp = httpx.get(url, timeout=_TIMEOUT_SECONDS)
    except httpx.TransportError:
        pytest.skip(f"Docker Hub registry unreachable, cannot verify {image!r}")

    assert resp.status_code == _HTTP_OK, (
        f"{engine_name}: pinned image {image!r} not found on Docker Hub "
        f"(library/{name}, tag {tag!r}, status {resp.status_code}) — {url}"
    )
