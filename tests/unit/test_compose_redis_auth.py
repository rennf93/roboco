"""Compose contract: Redis requires a password and every client carries it.

roboco-redis publishes 6379 on the host, so the data network was never its
only containment: anything that reaches the host (the LAN, every container
through the bridge gateway) reached an unauthenticated event bus. The server
now runs with ``--requirepass`` from a required ``.env`` value, and every
service that is pointed at it must be handed the same password.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from roboco.config import Settings

_REPO_ROOT = Path(__file__).parents[2]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.registry.yml")
_REQUIRED_PASSWORD = "${ROBOCO_REDIS_PASSWORD:?"


def _compose(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((_REPO_ROOT / name).read_text())
    return loaded


@pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
def test_redis_server_requires_the_env_password(compose_file: str) -> None:
    redis = _compose(compose_file)["services"]["redis"]
    command = redis["command"]
    assert command[command.index("--requirepass") + 1].startswith(_REQUIRED_PASSWORD)
    # The healthcheck's redis-cli authenticates from REDISCLI_AUTH.
    assert redis["environment"]["REDISCLI_AUTH"].startswith(_REQUIRED_PASSWORD)


@pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
def test_every_redis_client_service_gets_the_password(compose_file: str) -> None:
    clients = {
        name: service.get("environment") or {}
        for name, service in _compose(compose_file)["services"].items()
        if isinstance(service.get("environment"), dict)
        and "ROBOCO_REDIS_HOST" in service["environment"]
    }
    assert clients, "no service points at roboco-redis — the parse is broken"
    for name, env in clients.items():
        assert str(env.get("ROBOCO_REDIS_PASSWORD", "")).startswith(
            _REQUIRED_PASSWORD
        ), name


def test_redis_urls_carry_the_quoted_password() -> None:
    settings = Settings(
        redis_host="roboco-redis", redis_port=6379, redis_db=2, redis_password="a@b/c"
    )
    assert settings.redis_url == "redis://:a%40b%2Fc@roboco-redis:6379/2"
    assert settings.redis_url_for_db(0) == "redis://:a%40b%2Fc@roboco-redis:6379/0"


def test_redis_urls_without_a_password_stay_unauthenticated() -> None:
    settings = Settings(redis_host="localhost", redis_port=6379, redis_password=None)
    assert settings.redis_url_for_db(0) == "redis://localhost:6379/0"
