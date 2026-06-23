"""Coverage for roboco.config computed properties."""

from __future__ import annotations

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS


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


def test_batch_intake_enabled_defaults_off() -> None:
    """Sequenced batch intake is gated and inert by default."""
    assert Settings().batch_intake_enabled is False


def test_batch_intake_enabled_registered_in_feature_flags() -> None:
    """The flag is toggleable from the panel Feature Flags card."""
    assert "batch_intake_enabled" in {key for key, _ in FEATURE_FLAGS}
