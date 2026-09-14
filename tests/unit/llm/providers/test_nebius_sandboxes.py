"""nebius_sandboxes client — the Token Factory Sandboxes REST flow.

Covers the three-call contract (upload -> spawn -> poll) with a fake
httpx.AsyncClient: request shapes (octet-stream upload, bearer + Project
headers, disposable instance payload, files map), the Location-header
operation polling (terminal extraction, deadline cancel), result flattening
(metadata.result -> exit_code/stdout/stderr/cost), the never-raise
contract, and the composed shell expression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from roboco.config import settings
from roboco.llm.providers import nebius_sandboxes as ns

_KEY = "tf-key-1"
_BASE = "https://api.tokenfactory.nebius.com/sandboxes/v1"
_HTTP_CLIENT_ERROR = 400
_TIMEOUT = 900
_EXPECTED_COST = 0.5


class _FakeResponse:
    def __init__(
        self,
        json_data: Any = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._json = json_data if json_data is not None else {}
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= _HTTP_CLIENT_ERROR:
            request = httpx.Request("POST", _BASE)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._json


class _FakeClient:
    """Scripted async client: pops one response (or exception) per call."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _next(self) -> Any:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        return self._next()

    async def delete(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("DELETE", url, kwargs))
        return self._next()


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(ns.httpx, "AsyncClient", lambda **_kw: fake)


def _operation(status: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if result is not None:
        metadata["result"] = result
    return {
        "uuid": "op-1",
        "kind": "instance",
        "status": status,
        "error": None,
        "metadata": metadata,
    }


def _result(
    exit_code: int = 0, stdout: str = "1 passed", stderr: str = ""
) -> dict[str, Any]:
    return {
        "state": {"exit_code": exit_code, "timed_out": False},
        "stdout": {"value": stdout, "encoding": "ascii", "truncated": False},
        "stderr": {"value": stderr, "encoding": "ascii", "truncated": False},
        "resources": {"cost": 0.5, "elapsed_time": 12.5},
    }


# ---------------------------------------------------------------------------
# upload_sandbox_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_posts_octet_stream_with_auth_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_project_id", "proj-1")
    archive = tmp_path / "ws.tar.gz"
    archive.write_bytes(b"tarball-bytes")
    fake = _FakeClient([_FakeResponse({"uuid": "f-1", "sha256": "abc", "size": 13})])
    _patch_client(monkeypatch, fake)

    descriptor, error = await ns.upload_sandbox_file(_KEY, archive)

    assert error is None
    assert descriptor == {"uuid": "f-1", "sha256": "abc", "size": 13}
    method, url, kwargs = fake.calls[0]
    assert (method, url) == ("POST", "/files")
    assert kwargs["content"] == b"tarball-bytes"
    headers = kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {_KEY}"
    assert headers["Project"] == "proj-1"
    assert headers["Content-Type"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_upload_no_project_id_omits_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_project_id", "")
    archive = tmp_path / "ws.tar.gz"
    archive.write_bytes(b"x")
    fake = _FakeClient([_FakeResponse({"uuid": "f-1", "sha256": "a", "size": 1})])
    _patch_client(monkeypatch, fake)

    await ns.upload_sandbox_file(_KEY, archive)

    assert "Project" not in fake.calls[0][2]["headers"]


@pytest.mark.asyncio
async def test_upload_unreadable_file_never_raises() -> None:
    descriptor, error = await ns.upload_sandbox_file(
        _KEY, Path("/nonexistent/ws.tar.gz")
    )

    assert descriptor is None
    assert error is not None
    assert "could not read archive" in error


@pytest.mark.asyncio
async def test_upload_http_error_maps_to_error_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "ws.tar.gz"
    archive.write_bytes(b"x")
    fake = _FakeClient([_FakeResponse(status_code=503)])
    _patch_client(monkeypatch, fake)

    descriptor, error = await ns.upload_sandbox_file(_KEY, archive)

    assert descriptor is None
    assert error is not None
    assert "503" in error


# ---------------------------------------------------------------------------
# spawn_sandbox_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_payload_and_location_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_project_id", "")
    fake = _FakeClient(
        [
            _FakeResponse(
                {"uuid": "inst-1"},
                status_code=201,
                headers={"Location": "/v1/operations/op-1"},
            )
        ]
    )
    _patch_client(monkeypatch, fake)
    upload = {"uuid": "f-1", "sha256": "abc", "size": 1}

    spawn, error = await ns.spawn_sandbox_instance(
        _KEY,
        shell="tar -xzf /tmp/roboco-ws.tar.gz -C /workspace && pytest -q",
        image="tag:python:3.12",
        file_upload=upload,
        timeout_seconds=_TIMEOUT,
    )

    assert error is None
    assert spawn == {"instance_uuid": "inst-1", "operation_path": "/v1/operations/op-1"}
    _, url, kwargs = fake.calls[0]
    assert url == "/instances"
    body = kwargs["json"]
    assert body["shell"] is True
    assert body["disposable"] is True
    assert body["timeout"] == _TIMEOUT
    assert body["image"] == "tag:python:3.12"
    assert body["files"] == {"/tmp/roboco-ws.tar.gz": {"uuid": "f-1"}}


@pytest.mark.asyncio
async def test_spawn_401_maps_to_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([_FakeResponse(status_code=401)])
    _patch_client(monkeypatch, fake)

    spawn, error = await ns.spawn_sandbox_instance(
        _KEY, shell="true", image="tag:python:3.12"
    )

    assert spawn is None
    assert error is not None
    assert "auth failed" in error


@pytest.mark.asyncio
async def test_spawn_missing_location_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([_FakeResponse({"uuid": "inst-1"}, status_code=201)])
    _patch_client(monkeypatch, fake)

    spawn, error = await ns.spawn_sandbox_instance(
        _KEY, shell="true", image="tag:python:3.12"
    )

    assert spawn is None
    assert error is not None
    assert "no instance/operation" in error


# ---------------------------------------------------------------------------
# wait_sandbox_operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_returns_terminal_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ns, "_POLL_INTERVAL_SECONDS", 0)
    fake = _FakeClient(
        [
            _FakeResponse(_operation("EXECUTING")),
            _FakeResponse(_operation("SUCCESS", _result())),
        ]
    )
    _patch_client(monkeypatch, fake)

    body, error = await ns.wait_sandbox_operation(_KEY, "/v1/operations/op-1", 60)

    assert error is None
    assert body is not None
    assert body["status"] == "SUCCESS"
    assert body["metadata"]["result"]["state"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_wait_deadline_cancels_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ns, "_POLL_INTERVAL_SECONDS", 0)
    fake = _FakeClient(
        [
            _FakeResponse(_operation("EXECUTING")),
            _FakeResponse(_operation("EXECUTING")),
        ]
    )
    _patch_client(monkeypatch, fake)

    body, error = await ns.wait_sandbox_operation(_KEY, "/v1/operations/op-1", 0)

    assert body is None
    assert error is not None
    assert "polling deadline" in error
    assert any(
        method == "DELETE" and url == "/v1/operations/op-1"
        for method, url, _kw in fake.calls
    )


# ---------------------------------------------------------------------------
# run_sandbox_command — the composed flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sandbox_command_composes_upload_extract_and_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_project_id", "")
    archive = tmp_path / "ws.tar.gz"
    archive.write_bytes(b"tarball")
    fake = _FakeClient(
        [
            _FakeResponse({"uuid": "f-1", "sha256": "abc", "size": 7}),
            _FakeResponse(
                {"uuid": "inst-1"},
                status_code=201,
                headers={"Location": "/v1/operations/op-1"},
            ),
            _FakeResponse(_operation("SUCCESS", _result(exit_code=0))),
        ]
    )
    _patch_client(monkeypatch, fake)

    result, error = await ns.run_sandbox_command(
        _KEY,
        command="pytest -q",
        image="tag:python:3.12",
        archive_path=archive,
        timeout_seconds=600,
    )

    assert error is None
    assert result is not None
    assert result["exit_code"] == 0
    assert result["stdout"] == "1 passed"
    assert result["cost"] == _EXPECTED_COST
    assert result["instance_uuid"] == "inst-1"
    assert result["operation_uuid"] == "op-1"
    # The spawn shell chains extraction then the agent's command.
    _, _, spawn_kwargs = fake.calls[1]
    shell = spawn_kwargs["json"]["command"]
    assert "tar -xzf /tmp/roboco-ws.tar.gz -C /workspace" in shell
    assert shell.endswith("pytest -q")


@pytest.mark.asyncio
async def test_run_sandbox_command_failed_operation_is_error_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_project_id", "")
    archive = tmp_path / "ws.tar.gz"
    archive.write_bytes(b"tarball")
    failed = _operation("FAILED")
    failed["error"] = "image not found"
    fake = _FakeClient(
        [
            _FakeResponse({"uuid": "f-1", "sha256": "abc", "size": 7}),
            _FakeResponse(
                {"uuid": "inst-1"},
                status_code=201,
                headers={"Location": "/v1/operations/op-1"},
            ),
            _FakeResponse(failed),
        ]
    )
    _patch_client(monkeypatch, fake)

    result, error = await ns.run_sandbox_command(
        _KEY, command="pytest -q", archive_path=archive
    )

    assert result is None
    assert error is not None
    assert "FAILED" in error
    assert "image not found" in error


@pytest.mark.asyncio
async def test_client_never_raises_on_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([RuntimeError("boom")])
    _patch_client(monkeypatch, fake)

    spawn, error = await ns.spawn_sandbox_instance(
        _KEY, shell="true", image="tag:python:3.12"
    )

    assert spawn is None
    assert error is not None
    assert "unexpected error" in error
