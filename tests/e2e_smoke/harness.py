"""e2e smoke harness — in-process RoboCo stack + scripted-agent driver.

Pieces (all REAL except GitHub and the LLM):

- The API: the real v1 flow/do routers + real middleware/exception handlers,
  served by uvicorn in a thread, over the ephemeral test Postgres (the app's
  own lazy engine is pointed at it by patching ``settings.database_*`` and
  resetting ``_DbHolder``).
- Git: a local bare origin whose path CONTAINS ``github.com/<owner>/<repo>``
  — ``_parse_git_url`` extracts owner/repo from it while clone/fetch/push
  run tokenless over the local protocol.
- GitHub REST: a fake ``/_github`` router mounted on the same app
  (``settings.github_api_base_url`` points at it). PR state lives in memory;
  merges perform REAL git merges (squash included) on the bare origin, so
  downstream git logic (cherry checks, freshness, branch sync) sees reality.
- Agents: ``ScriptedAgent`` reloads the REAL ``roboco.mcp.flow_server`` /
  ``do_server`` modules with that agent's env (id, role, role-scoped
  manifest built from the real ``role_config``) and calls the REAL tool
  functions, which POST to the in-process API over loopback HTTP.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import uvicorn
from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType
    from uuid import UUID

_OWNER = "e2e-smoke"
_REPO = "proj"


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


# ---------------------------------------------------------------------------
# Fake GitHub REST — PR state in memory, merges as REAL git ops on the origin
# ---------------------------------------------------------------------------


@dataclass
class _FakeGitHub:
    origin: Path
    admin_clone: Path
    prs: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    next_number: int = 1

    def create_pr(self, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        number = self.next_number
        self.next_number += 1
        pr = {
            "number": number,
            "html_url": f"https://github.com/{_OWNER}/{_REPO}/pull/{number}",
            "title": title,
            "body": body,
            "state": "open",
            "merged": False,
            "head": {
                "ref": head,
                "sha": self._sha_of(head),
                "repo": {"full_name": f"{_OWNER}/{_REPO}"},
            },
            "base": {"ref": base},
            "user": {"login": "e2e-bot"},
            "author_association": "MEMBER",
        }
        self.prs[number] = pr
        return pr

    def _sha_of(self, branch: str) -> str:
        try:
            return _git(self.origin, "rev-parse", branch)
        except subprocess.CalledProcessError:
            return "0" * 40

    def merge_pr(self, number: int, merge_method: str) -> dict[str, Any]:
        pr = self.prs[number]
        head, base = pr["head"]["ref"], pr["base"]["ref"]
        admin = self.admin_clone
        _git(admin, "fetch", "origin", "--prune")
        _git(admin, "checkout", "-B", base, f"origin/{base}")
        if merge_method == "squash":
            _git(admin, "merge", "--squash", f"origin/{head}")
            _git(admin, "commit", "-m", f"{pr['title']} (#{number})")
        else:
            _git(
                admin,
                "merge",
                "--no-ff",
                "-m",
                f"Merge pull request #{number} from {head}",
                f"origin/{head}",
            )
        _git(admin, "push", "origin", base)
        sha = _git(admin, "rev-parse", "HEAD")
        pr["merged"] = True
        pr["state"] = "closed"
        return {
            "merged": True,
            "sha": sha,
            "message": "Pull Request successfully merged",
        }

    def open_prs(self, head: str | None, base: str | None) -> list[dict[str, Any]]:
        out = []
        for pr in self.prs.values():
            if pr["state"] != "open":
                continue
            if head and pr["head"]["ref"] != head.split(":", 1)[-1]:
                continue
            if base and pr["base"]["ref"] != base:
                continue
            out.append(pr)
        return out


def _fake_github_router(gh: _FakeGitHub) -> APIRouter:
    r = APIRouter(prefix="/_github")

    @r.get("/repos/{owner}/{repo}")
    async def repo_caps(owner: str, repo: str) -> dict[str, Any]:
        return {
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
        }

    @r.get("/repos/{owner}/{repo}/pulls/{number}")
    async def get_pr(owner: str, repo: str, number: int) -> JSONResponse:
        pr = gh.prs.get(number)
        if pr is None:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        # Real GitHub recomputes head.sha as the branch advances; a stale
        # creation-time snapshot broke the unchanged-PR gate's semantics.
        pr["head"]["sha"] = gh._sha_of(pr["head"]["ref"])
        return JSONResponse(pr)

    @r.get("/repos/{owner}/{repo}/pulls")
    async def list_prs(
        owner: str,
        repo: str,
        head: str | None = None,
        base: str | None = None,
        state: str = "open",
    ) -> list[dict[str, Any]]:
        return gh.open_prs(head, base)

    @r.post("/repos/{owner}/{repo}/pulls", status_code=201)
    async def create_pr(owner: str, repo: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return gh.create_pr(
            body["title"], body.get("body", ""), body["head"], body["base"]
        )

    @r.patch("/repos/{owner}/{repo}/pulls/{number}")
    async def patch_pr(
        owner: str, repo: str, number: int, request: Request
    ) -> JSONResponse:
        pr = gh.prs.get(number)
        if pr is None:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        body = await request.json()
        for key in ("title", "body", "state"):
            if key in body:
                pr[key] = body[key]
        return JSONResponse(pr)

    @r.put("/repos/{owner}/{repo}/pulls/{number}/merge")
    async def merge_pr(
        owner: str, repo: str, number: int, request: Request
    ) -> JSONResponse:
        if number not in gh.prs:
            return JSONResponse({"message": "Not Found"}, status_code=404)
        body = await request.json()
        try:
            result = gh.merge_pr(number, body.get("merge_method", "merge"))
        except subprocess.CalledProcessError as exc:
            return JSONResponse(
                {"message": f"Merge conflict: {exc.stderr}"}, status_code=409
            )
        return JSONResponse(result)

    @r.post("/repos/{owner}/{repo}/pulls/{number}/requested_reviewers", status_code=201)
    async def request_reviewers(
        owner: str, repo: str, number: int, request: Request
    ) -> dict[str, Any]:
        return gh.prs.get(number, {})

    @r.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
    async def comment(
        owner: str, repo: str, number: int, request: Request
    ) -> dict[str, Any]:
        gh.comments.append({"number": number, "body": (await request.json())})
        return {"id": len(gh.comments)}

    @r.delete("/repos/{owner}/{repo}/git/refs/heads/{branch:path}", status_code=204)
    async def delete_branch(owner: str, repo: str, branch: str) -> None:
        with suppress(subprocess.CalledProcessError):
            _git(gh.origin, "branch", "-D", branch)

    return r


# ---------------------------------------------------------------------------
# Stack: settings patches + origin + app + uvicorn thread
# ---------------------------------------------------------------------------


@dataclass
class E2EStack:
    base_url: str
    root: Path
    origin: Path
    workspaces_root: Path
    db_url: str
    github: _FakeGitHub

    def workspace_of(self, project_slug: str, team: str, agent_slug: str) -> Path:
        return self.workspaces_root / project_slug / team / agent_slug

    def run_db(self, coro_fn: Any) -> Any:
        """Run ``coro_fn(session)`` against a fresh engine/session and return."""

        async def _run() -> Any:
            engine = create_async_engine(self.db_url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as session:
                    result = await coro_fn(session)
                    await session.commit()
                    return result
            finally:
                await engine.dispose()

        return asyncio.run(_run())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _seed_origin(root: Path) -> Path:
    """Bare origin at a path _parse_git_url can read owner/repo from."""
    origin = root / "github.com" / _OWNER / f"{_REPO}.git"
    origin.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=master", str(origin)],
        check=True,
        capture_output=True,
    )
    seed = root / "seed-clone"
    subprocess.run(
        ["git", "clone", str(origin), str(seed)], check=True, capture_output=True
    )
    _git(seed, "config", "user.name", "roboco-e2e")
    _git(seed, "config", "user.email", "e2e@roboco.local")
    (seed / "README.md").write_text("# e2e smoke project\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "Initial commit")
    _git(seed, "push", "origin", "master")
    return origin


def _make_admin_clone(root: Path, origin: Path) -> Path:
    admin = root / "gh-admin-clone"
    subprocess.run(
        ["git", "clone", str(origin), str(admin)], check=True, capture_output=True
    )
    _git(admin, "config", "user.name", "fake-github")
    _git(admin, "config", "user.email", "merge@github.local")
    return admin


def _build_app(gh: _FakeGitHub) -> FastAPI:
    from roboco.api.middleware import setup_middleware
    from roboco.api.routes.health import router as health_router
    from roboco.api.routes.orchestrator import router as orchestrator_router
    from roboco.api.routes.settings import router as settings_router
    from roboco.api.routes.tasks import router as tasks_router
    from roboco.api.routes.v1 import do as do_module
    from roboco.api.routes.v1 import flow_auditor as fa
    from roboco.api.routes.v1 import flow_board as fb
    from roboco.api.routes.v1 import flow_cell_pm as fcp
    from roboco.api.routes.v1 import flow_dev as fd
    from roboco.api.routes.v1 import flow_doc as fdoc
    from roboco.api.routes.v1 import flow_main_pm as fmp
    from roboco.api.routes.v1 import flow_pr_reviewer as fpr
    from roboco.api.routes.v1 import flow_qa as fq

    app = FastAPI(title="roboco-e2e-smoke")
    setup_middleware(app)
    app.include_router(health_router)
    for module in (fd, fq, fdoc, fcp, fmp, fb, fa, fpr):
        app.include_router(module.router)
    app.include_router(do_module.router)
    # The REST task surface — scenario 3 drives the real CEO
    # approve-and-merge endpoint (the human gate) through it.
    app.include_router(tasks_router, prefix="/api/tasks")
    # Cloud-auth gate coverage smoke exercises the real _require_ceo and
    # require_panel_token dep paths on these routers.
    app.include_router(orchestrator_router, prefix="/api/orchestrator")
    app.include_router(settings_router, prefix="/api/settings")
    app.include_router(_fake_github_router(gh))
    return app


def build_e2e_stack(
    _test_database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[E2EStack]:
    """Generator behind the ``e2e_stack`` fixture (defined in conftest)."""
    from roboco.config import settings
    from roboco.db import base as db_base

    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("e2e")
    origin = _seed_origin(root)
    admin = _make_admin_clone(root, origin)
    gh = _FakeGitHub(origin=origin, admin_clone=admin)
    workspaces = root / "workspaces"
    workspaces.mkdir()

    url = make_url(_test_database_url)
    mp.setattr(settings, "database_host", url.host or "localhost")
    mp.setattr(settings, "database_port", url.port or 5432)
    mp.setattr(settings, "database_user", url.username or "")
    mp.setattr(settings, "database_password", url.password or "")
    mp.setattr(settings, "database_name", url.database or "")
    mp.setattr(settings, "workspaces_root", str(workspaces))
    mp.setattr(settings, "workspace_auto_clone", True)
    mp.setattr(settings, "encryption_key", Fernet.generate_key().decode())

    # The app's lazy engine must bind to the patched settings, not a leftover.
    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    mp.setattr(settings, "github_api_base_url", f"{base_url}/_github")

    app = _build_app(gh)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import httpx

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            # Any HTTP response at all means the server thread is up.
            httpx.get(f"{base_url}/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("e2e app server did not become ready")

    try:
        yield E2EStack(
            base_url=base_url,
            root=root,
            origin=origin,
            workspaces_root=workspaces,
            db_url=_test_database_url,
            github=gh,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        db_base._DbHolder.engine = None
        db_base._DbHolder.session_factory = None
        mp.undo()


# ---------------------------------------------------------------------------
# Scripted agents — the REAL MCP tool functions, per-agent module reloads
# ---------------------------------------------------------------------------


class ScriptedAgent:
    """Drives the real flow/do MCP tool functions as one seeded agent."""

    def __init__(self, stack: E2EStack, agent_id: UUID, slug: str, role: str) -> None:
        self.stack = stack
        self.agent_id = agent_id
        self.slug = slug
        self.role = role
        self._manifest_path = stack.root / f"manifest-{slug}.json"
        self._manifest_path.write_text(json.dumps(self._manifest()))

    def _manifest(self) -> dict[str, Any]:
        from roboco.services.gateway.role_config import get_role_config

        cfg = get_role_config(self.role)
        return {
            "agent_id": str(self.agent_id),
            "role": self.role,
            "team": "backend",
            "workspace_path": str(self.stack.workspaces_root),
            "flow_tools": list(cfg.flow_tools),
            "do_tools": list(cfg.do_tools),
            "read_tools": ["Read", "Glob", "Grep"],
            "write_tools": ["Edit", "Write"] if cfg.allows_write else [],
            "bash_allowed": True,
            "subagent_allowed": False,
            "subagent_model": None,
            "env": {},
        }

    def _module(self, name: str) -> ModuleType:
        os.environ["ROBOCO_AGENT_ID"] = str(self.agent_id)
        os.environ["ROBOCO_AGENT_ROLE"] = self.role
        os.environ["ROBOCO_ORCHESTRATOR_URL"] = self.stack.base_url
        os.environ["ROBOCO_TOOL_MANIFEST_PATH"] = str(self._manifest_path)
        module = importlib.import_module(name)
        if getattr(module, "AGENT_ID", None) != str(self.agent_id):
            module = importlib.reload(module)
        return module

    def flow(self, verb: str, /, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = getattr(self._module("roboco.mcp.flow_server"), verb)(
            **kwargs
        )
        return result

    def do(self, tool: str, /, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = getattr(self._module("roboco.mcp.do_server"), tool)(
            **kwargs
        )
        return result


def expect_error(env: dict[str, Any], kind: str, context: str) -> dict[str, Any]:
    """Assert an envelope is the EXPECTED rejection kind."""
    assert env.get("error") == kind, (
        f"{context}: expected rejection {kind!r}, got error={env.get('error')!r}\n"
        f"  full: {json.dumps(env, default=str, indent=2)[:4000]}"
    )
    return env


def expect_ok(env: dict[str, Any], context: str) -> dict[str, Any]:
    """Assert an envelope is a success; on failure show the whole envelope."""
    assert isinstance(env, dict), f"{context}: non-dict envelope: {env!r}"
    assert not env.get("error"), (
        f"{context}: rejected with error={env.get('error')!r}\n"
        f"  message : {env.get('message')}\n"
        f"  remediate: {env.get('remediate')}\n"
        f"  missing : {env.get('missing')}\n"
        f"  full    : {json.dumps(env, default=str, indent=2)[:4000]}"
    )
    return env
