"""MinIO client coverage: unconfigured guard + endpoint scheme parsing (mocks)."""

from __future__ import annotations

from typing import Any

import pytest
from roboco.config import settings as cfg
from roboco.services import minio_client


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Each test rebuilds the singleton (the test-isolation hazard)."""
    minio_client._reset_client()
    yield
    minio_client._reset_client()


def test_get_client_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "minio_endpoint", "")
    monkeypatch.setattr(cfg, "minio_access_key", "")
    monkeypatch.setattr(cfg, "minio_secret_key", "")
    assert minio_client.get_client() is None


def test_get_client_parses_endpoint_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """http://roboco-minio:9000 → endpoint=roboco-minio:9000, secure=False."""
    built: dict[str, Any] = {}

    class FakeMinio:
        def __init__(
            self,
            *,
            endpoint: str,
            access_key: str | None,
            secret_key: str | None,
            secure: bool,
            region: str | None,
        ) -> None:
            built["endpoint"] = endpoint
            built["access_key"] = access_key
            built["secret_key"] = secret_key
            built["secure"] = secure
            built["region"] = region

    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(cfg, "minio_endpoint", "http://roboco-minio:9000")
    monkeypatch.setattr(cfg, "minio_access_key", "minio")
    monkeypatch.setattr(cfg, "minio_secret_key", "minio123")
    monkeypatch.setattr(cfg, "minio_region", "us-east-1")

    client = minio_client.get_client()
    assert isinstance(client, FakeMinio)
    assert built["endpoint"] == "roboco-minio:9000"
    assert built["secure"] is False
    assert built["access_key"] == "minio"
    assert built["secret_key"] == "minio123"
    assert built["region"] == "us-east-1"


def test_get_client_parses_https_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, Any] = {}

    class FakeMinio:
        def __init__(self, **kwargs: Any) -> None:
            built["endpoint"] = kwargs["endpoint"]
            built["secure"] = kwargs["secure"]

    monkeypatch.setattr(minio_client, "Minio", FakeMinio)
    monkeypatch.setattr(cfg, "minio_endpoint", "https://minio.example:9000")
    minio_client._reset_client()
    minio_client.get_client()
    assert built["endpoint"] == "minio.example:9000"
    assert built["secure"] is True


def test_put_object_noops_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "minio_endpoint", "")
    minio_client._reset_client()
    # Must not raise — the write path's upstream guard is the source of truth,
    # but this stays defensive.
    minio_client.put_object(b"bytes", "key.mp4")


def test_get_object_stream_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "minio_endpoint", "")
    minio_client._reset_client()
    with pytest.raises(RuntimeError, match="unconfigured"):
        next(minio_client.get_object_stream("key.mp4"))


def test_get_client_singleton_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "minio_endpoint", "http://roboco-minio:9000")
    minio_client._reset_client()
    a = minio_client.get_client()
    b = minio_client.get_client()
    assert a is b
