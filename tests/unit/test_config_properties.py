"""Coverage for roboco.config computed properties."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError
from roboco.config import Settings, resolve_uvicorn_loop_factory


def test_internal_api_url_uses_api_url_when_set() -> None:
    s = Settings(api_url="http://orchestrator:8000")
    assert s.internal_api_url == "http://orchestrator:8000/api"


def test_internal_api_url_strips_trailing_slash_on_api_url() -> None:
    s = Settings(api_url="http://orchestrator:8000/")
    assert s.internal_api_url == "http://orchestrator:8000/api"


def test_internal_api_url_uses_host_when_api_url_unset() -> None:
    s = Settings(api_url=None, host="localhost", port=9000)
    assert s.internal_api_url == "http://localhost:9000/api"


def test_internal_api_url_swaps_bind_all_to_localhost() -> None:
    """Line 70: host='0.0.0.0' becomes '127.0.0.1' for connecting."""
    s = Settings(api_url=None, host="0.0.0.0", port=8000)
    assert s.internal_api_url == "http://127.0.0.1:8000/api"


def test_redis_url_with_password_includes_credential() -> None:
    """Line 118: password present → includes :pw@ in URL."""
    s = Settings(
        redis_host="redis", redis_port=6379, redis_db=2, redis_password="secret"
    )
    assert s.redis_url == "redis://:secret@redis:6379/2"


def test_redis_url_without_password() -> None:
    s = Settings(redis_host="redis", redis_port=6379, redis_db=0, redis_password=None)
    assert s.redis_url == "redis://redis:6379/0"


# ---------------------------------------------------------------------------
# Cloud auth — fail-loud secret validation
# ---------------------------------------------------------------------------


def test_cloud_auth_off_does_not_require_secret() -> None:
    """Default (off) construction never raises, secret or not."""
    s = Settings(cloud_auth_enabled=False, cloud_auth_secret=None)
    assert s.cloud_auth_enabled is False


def test_cloud_auth_enabled_without_secret_fails_loud() -> None:
    """Arming cloud auth with no session-signing secret must fail at startup,
    not silently mint unsigned/unsafe sessions."""
    with pytest.raises(ValueError, match="ROBOCO_CLOUD_AUTH_SECRET"):
        Settings(cloud_auth_enabled=True, cloud_auth_secret=None)


def test_cloud_auth_enabled_with_secret_succeeds() -> None:
    s = Settings(cloud_auth_enabled=True, cloud_auth_secret="s" * 32)
    assert s.cloud_auth_enabled is True
    assert s.cloud_auth_secret == "s" * 32


def test_cloud_auth_cookie_max_age_defaults_to_30_days() -> None:
    s = Settings()
    assert s.cloud_auth_cookie_max_age == 30 * 24 * 60 * 60


def test_cloud_auth_rejects_panel_agent_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_CLOUD_AUTH_ENABLED", "true")
    monkeypatch.setenv("ROBOCO_CLOUD_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("ROBOCO_PANEL_AGENT_TOKEN", "some-signed-token")
    with pytest.raises(ValueError, match="ROBOCO_PANEL_AGENT_TOKEN"):
        Settings()


def test_cloud_auth_ok_without_panel_agent_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_CLOUD_AUTH_ENABLED", "true")
    monkeypatch.setenv("ROBOCO_CLOUD_AUTH_SECRET", "x" * 32)
    monkeypatch.delenv("ROBOCO_PANEL_AGENT_TOKEN", raising=False)
    s = Settings()
    assert s.cloud_auth_enabled is True


# ---------------------------------------------------------------------------
# local_llm_base_url — internal-host guard (H13)
# ---------------------------------------------------------------------------


def test_local_llm_base_url_default_accepted() -> None:
    s = Settings()
    assert s.local_llm_base_url == "http://roboco-ollama:11434/v1"


def test_local_llm_base_url_localhost_accepted() -> None:
    s = Settings(local_llm_base_url="http://localhost:11434")
    assert s.local_llm_base_url == "http://localhost:11434"


def test_local_llm_base_url_rfc1918_accepted() -> None:
    s = Settings(local_llm_base_url="http://10.0.0.5:11434")
    assert s.local_llm_base_url == "http://10.0.0.5:11434"


def test_local_llm_base_url_ipv6_loopback_accepted() -> None:
    s = Settings(local_llm_base_url="http://[::1]:11434")
    assert s.local_llm_base_url == "http://[::1]:11434"


def test_local_llm_base_url_cluster_local_accepted() -> None:
    s = Settings(local_llm_base_url="http://ollama.default.svc.cluster.local:11434")
    assert s.local_llm_base_url == "http://ollama.default.svc.cluster.local:11434"


def test_local_llm_base_url_public_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(local_llm_base_url="https://api.openai.com/v1")


def test_local_llm_base_url_missing_host_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(local_llm_base_url="http://")


# ---------------------------------------------------------------------------
# uvicorn_loop — default asyncio, uvloop opt-in (CI segfault fix)
# ---------------------------------------------------------------------------


def test_uvicorn_loop_defaults_to_asyncio() -> None:
    assert Settings().uvicorn_loop == "asyncio"


def test_uvicorn_loop_honors_constructor_override() -> None:
    assert Settings(uvicorn_loop="uvloop").uvicorn_loop == "uvloop"


def test_uvicorn_loop_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_UVICORN_LOOP", "uvloop")
    assert Settings().uvicorn_loop == "uvloop"


def test_uvicorn_loop_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(uvicorn_loop="unknown")  # type: ignore[arg-type]


def test_resolve_uvicorn_loop_factory_asyncio_is_none() -> None:
    """The default: no override, so asyncio.run() picks its own stock loop."""
    assert resolve_uvicorn_loop_factory("asyncio") is None


def test_resolve_uvicorn_loop_factory_uvloop_returns_new_event_loop() -> None:
    factory = resolve_uvicorn_loop_factory("uvloop")
    assert factory is not None
    loop = factory()
    try:
        uvloop = importlib.import_module("uvloop")
        assert isinstance(loop, uvloop.Loop)
    finally:
        loop.close()
