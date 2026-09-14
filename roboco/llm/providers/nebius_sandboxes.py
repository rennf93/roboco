"""Token Factory Sandboxes client - ephemeral microVM code execution.

Nebius Token Factory's Sandboxes API (the "Contree" service,
``https://api.tokenfactory.nebius.com/sandboxes``) runs a command inside an
ephemeral, isolated microVM (Cloud Hypervisor) that is destroyed after the
execution: the agent uploads files, the VM boots the requested image, runs
one shell expression, and reports ``exit_code`` / ``stdout`` / ``stderr``
plus metered resources. RoboCo uses it for exactly one workload - the
QA-scoped ``run_sandbox_tests`` gateway verb executing a task's test suite
outside the agent container - so this client implements the minimal
three-call flow and nothing more:

  1. ``POST /v1/files`` - upload the workspace ``git archive`` as an
     octet-stream body; the response carries the content-addressed
     ``{uuid, sha256, size}``.
  2. ``POST /v1/instances`` - spawn the one-shot execution referencing the
     uploaded file uuid in ``files``; the ``Location`` header names the
     operation to poll.
  3. ``GET /v1/operations/{id}`` - poll until the status leaves
     ``{PENDING, ASSIGNED, EXECUTING}``; the terminal body carries
     ``metadata.result`` (an ``InstanceResult``: ``state.exit_code``,
     ``stdout``/``stderr`` streams, ``resources.cost``).

Auth mirrors the Sandboxes OpenAPI contract: ``Authorization: Bearer`` with
the SAME stored Nebius API key the inference provider uses, plus the
optional ``Project`` header when ``settings.token_factory_sandboxes_project_id``
is set (scoped accounts). Live-verification of both assumptions against a
real key is a documented bring-up item (docs/backend/api/token-factory-sandboxes.md).

Shape discipline mirrors the probe functions in ``roboco.services.llm``:
every function returns ``(data, None)`` on success or ``(None, error_str)``
on any failure and NEVER raises - a sandbox outage must degrade to an
envelope rejection, never a 500.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from roboco.config import settings

if TYPE_CHECKING:
    from pathlib import Path

_log = structlog.get_logger(__name__)

# Per-HTTP-call ceilings: a stalled Sandboxes endpoint must surface as an
# error string quickly, not hang the gateway verb's own request timeout.
_CONNECT_TIMEOUT = 15.0
_REQUEST_TIMEOUT = 60.0

# Poll cadence for the operation status (the VM boots in milliseconds per
# Nebius, but the queued execution itself runs to completion - test suites
# take minutes; a 3s poll adds at most one interval of latency).
_POLL_INTERVAL_SECONDS = 3.0

# Terminal operation statuses (GET /v1/operations/{id} "status" enum).
_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED"})

# Sandbox-side paths the verb's shell expression uses. The uploaded archive
# lands here (referenced by the spawn request's ``files`` map) and is
# extracted into the workspace dir before the agent's command runs.
_SANDBOX_ARCHIVE_PATH = "/tmp/roboco-ws.tar.gz"
_SANDBOX_WORKSPACE_DIR = "/workspace"

# Evidence tails: a test suite's stdout can be megabytes; the envelope
# carries only the tail (the full log lives in the journal entry).
_EVIDENCE_TAIL_CHARS = 4000


def _headers(api_key: str) -> dict[str, str]:
    """Bearer + optional Project header (scoped accounts)."""
    headers = {"Authorization": f"Bearer {api_key}"}
    project_id = settings.token_factory_sandboxes_project_id
    if project_id:
        headers["Project"] = project_id
    return headers


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.token_factory_sandboxes_base_url.rstrip("/") + "/v1",
        timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=_CONNECT_TIMEOUT),
    )


def _transport_error(context: str) -> str:
    return f"connection to Token Factory Sandboxes failed while {context}"


async def upload_sandbox_file(
    api_key: str, path: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """Upload one file (``POST /v1/files``, octet-stream) and return its
    ``{uuid, sha256, size}`` descriptor, or ``(None, error)``."""
    data: bytes | None = None
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"could not read archive {path}: {exc}"
    resp: httpx.Response | None = None
    error: str | None = None
    try:
        async with _client() as client:
            resp = await client.post(
                "/files",
                content=data,
                headers={
                    **_headers(api_key),
                    "Content-Type": "application/octet-stream",
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        error = _transport_error("uploading the workspace archive")
    except httpx.ConnectError:
        error = _transport_error("uploading the workspace archive")
    except httpx.HTTPStatusError as exc:
        error = (
            f"Token Factory Sandboxes upload failed with HTTP "
            f"{exc.response.status_code}"
        )
    except Exception as exc:
        _log.error(
            "Unexpected error uploading sandbox file",
            error=exc.__class__.__name__,
        )
        error = "an unexpected error occurred uploading the sandbox file"
    if error is not None or resp is None:
        return None, error
    body = resp.json()
    if not isinstance(body, dict) or not body.get("uuid"):
        return None, "Token Factory Sandboxes upload returned no file uuid"
    return body, None


async def spawn_sandbox_instance(
    api_key: str,
    *,
    shell: str,
    image: str,
    file_upload: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Spawn the one-shot execution (``POST /v1/instances``).

    ``shell`` is the full shell expression the microVM runs. ``file_upload``
    (an ``upload_sandbox_file`` descriptor) lands at
    ``_SANDBOX_ARCHIVE_PATH`` inside the VM. Returns
    ``{"instance_uuid", "operation_path"}`` where ``operation_path`` is the
    relative operation URL from the ``Location`` header, or ``(None,
    error)``.
    """
    files_map: dict[str, Any] = {}
    if file_upload is not None:
        files_map[_SANDBOX_ARCHIVE_PATH] = {"uuid": file_upload["uuid"]}
    effective_timeout = (
        timeout_seconds or settings.token_factory_sandboxes_timeout_seconds
    )
    payload: dict[str, Any] = {
        "command": shell,
        "image": image,
        "shell": True,
        "disposable": True,
        # No cwd: the workspace dir doesn't exist until the shell chain
        # extracts the archive (and a nonexistent cwd could fail the spawn);
        # the chain's `cd` covers it.
        "truncate_output_at": _EVIDENCE_TAIL_CHARS * 8,
        "timeout": effective_timeout,
    }
    if files_map:
        payload["files"] = files_map
    resp: httpx.Response | None = None
    error: str | None = None
    try:
        async with _client() as client:
            resp = await client.post(
                "/instances", json=payload, headers=_headers(api_key)
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        error = _transport_error("spawning the sandbox run")
    except httpx.ConnectError:
        error = _transport_error("spawning the sandbox run")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:  # noqa: PLR2004
            error = (
                "Token Factory Sandboxes auth failed - the Nebius API key "
                "may be invalid or lack Sandboxes access"
            )
        elif status == 403:  # noqa: PLR2004
            error = (
                "Token Factory Sandboxes refused the request (403) - check "
                "the key's permissions and the Project header setting"
            )
        else:
            error = f"Token Factory Sandboxes spawn failed with HTTP {status}"
    except Exception as exc:
        _log.error(
            "Unexpected error spawning sandbox instance",
            error=exc.__class__.__name__,
        )
        error = "an unexpected error occurred spawning the sandbox"
    if error is not None or resp is None:
        return None, error
    body = resp.json()
    instance_uuid = body.get("uuid") if isinstance(body, dict) else None
    operation_path = resp.headers.get("Location")
    if not instance_uuid or not operation_path:
        return None, "Token Factory Sandboxes spawn returned no instance/operation"
    return {"instance_uuid": instance_uuid, "operation_path": operation_path}, None


async def wait_sandbox_operation(
    api_key: str,
    operation_path: str,
    deadline_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Poll the operation until a terminal status, and return the terminal
    operation body (``metadata.result`` carries the InstanceResult), or
    ``(None, error)``.

    On deadline the operation is cancelled best-effort
    (``DELETE /v1/operations/{id}``) so a hung run cannot keep metering.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    body: dict[str, Any] | None = None
    error: str | None = None
    try:
        async with _client() as client:
            op_url = (
                operation_path
                if operation_path.startswith("/")
                else f"/{operation_path}"
            )
            while True:
                resp = await client.get(op_url, headers=_headers(api_key))
                resp.raise_for_status()
                body = resp.json()
                status = body.get("status") if isinstance(body, dict) else None
                if status in _TERMINAL_STATUSES:
                    break
                if loop.time() >= deadline:
                    with contextlib.suppress(Exception):
                        await client.delete(op_url, headers=_headers(api_key))
                    error = (
                        "sandbox run exceeded the polling deadline and was "
                        "cancelled - raise "
                        "ROBOCO_TOKEN_FACTORY_SANDBOXES_TIMEOUT_SECONDS if "
                        "the suite legitimately needs longer"
                    )
                    break
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except httpx.TimeoutException:
        error = _transport_error("polling the sandbox run")
    except httpx.ConnectError:
        error = _transport_error("polling the sandbox run")
    except httpx.HTTPStatusError as exc:
        error = (
            f"Token Factory Sandboxes operation poll failed with HTTP "
            f"{exc.response.status_code}"
        )
    except Exception as exc:
        _log.error(
            "Unexpected error polling sandbox operation",
            error=exc.__class__.__name__,
        )
        error = "an unexpected error occurred polling the sandbox run"
    if error is not None:
        return None, error
    return body, None


def _stream_value(stream: Any) -> str:
    """Extract the text of a stdout/stderr stream object
    (``{value, encoding, truncated}``); tolerant of a bare string."""
    if isinstance(stream, str):
        return stream
    if isinstance(stream, dict):
        value = stream.get("value")
        return value if isinstance(value, str) else ""
    return ""


def _extract_run_result(operation: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the flattened run result off a terminal operation body, or None
    when the shape is not what the spec promises."""
    if not isinstance(operation, dict) or operation.get("status") != "SUCCESS":
        return None
    metadata = operation.get("metadata")
    result = metadata.get("result") if isinstance(metadata, dict) else None
    if not isinstance(result, dict):
        return None
    state = result.get("state") or {}
    resources = result.get("resources") or {}
    return {
        "exit_code": state.get("exit_code"),
        "timed_out": bool(state.get("timed_out")),
        "stdout": _stream_value(result.get("stdout")),
        "stderr": _stream_value(result.get("stderr")),
        "cost": resources.get("cost"),
        "elapsed_time": resources.get("elapsed_time"),
    }


async def run_sandbox_command(
    api_key: str,
    *,
    command: str,
    image: str | None = None,
    archive_path: Path | None = None,
    timeout_seconds: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The verb's one-call entry point: upload the archive (when given),
    spawn the disposable instance that extracts it and runs ``command``,
    poll to completion, and return the flattened result
    (``{exit_code, timed_out, stdout, stderr, cost, elapsed_time,
    instance_uuid, operation_uuid}``), or ``(None, error)``.

    The shell expression is composed HERE so the verb passes only the
    agent's test command; a failed extraction fails the chain (``&&``) and
    surfaces as a non-zero exit code with tar's stderr.
    """
    file_upload: dict[str, Any] | None = None
    if archive_path is not None:
        file_upload, err = await upload_sandbox_file(api_key, archive_path)
        if err is not None or file_upload is None:
            return None, err
    extract = ""
    if file_upload is not None:
        extract = (
            f"mkdir -p {_SANDBOX_WORKSPACE_DIR} && "
            f"tar -xzf {_SANDBOX_ARCHIVE_PATH} -C {_SANDBOX_WORKSPACE_DIR} && "
            f"cd {_SANDBOX_WORKSPACE_DIR} && "
        )
    spawn, err = await spawn_sandbox_instance(
        api_key,
        shell=f"{extract}{command}",
        image=image or settings.token_factory_sandboxes_image,
        file_upload=file_upload,
        timeout_seconds=timeout_seconds,
    )
    if err is not None or spawn is None:
        return None, err
    effective_timeout = (
        timeout_seconds or settings.token_factory_sandboxes_timeout_seconds
    )
    # Poll past the in-VM kill by a margin so the timeout result itself
    # (timed_out=true) arrives instead of the poll deadline racing it.
    operation, err = await wait_sandbox_operation(
        api_key,
        spawn["operation_path"],
        deadline_seconds=effective_timeout + 120.0,
    )
    if err is not None or operation is None:
        return None, err
    flattened = _extract_run_result(operation)
    if flattened is None:
        error_text = operation.get("error") or "unknown sandbox failure"
        return None, f"sandbox run ended {operation.get('status')}: {error_text}"
    flattened["instance_uuid"] = spawn["instance_uuid"]
    flattened["operation_uuid"] = operation.get("uuid")
    return flattened, None


def tail_for_evidence(text: str) -> str:
    """The evidence payload's stdout/stderr tail (keeps envelopes bounded)."""
    return text[-_EVIDENCE_TAIL_CHARS:]
