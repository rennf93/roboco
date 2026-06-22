"""Modularity checks: cohesion, thin routes, thin components, god class."""

from __future__ import annotations

from roboco.conventions import classify_python, classify_ts
from roboco.conventions.modularity import check_modularity
from roboco.foundation.policy.conventions.models import ConventionsStandard


def _py(src: str) -> set[str]:
    source = src.encode()
    defs = classify_python.classify_definitions(source)
    findings = check_modularity(
        "app/x.py", defs, source, "python", ConventionsStandard()
    )
    return {f.rule for f in findings}


def _ts(src: str, lang: str = "tsx") -> set[str]:
    source = src.encode()
    defs = classify_ts.classify_definitions(source, lang)
    findings = check_modularity("src/x.tsx", defs, source, lang, ConventionsStandard())
    return {f.rule for f in findings}


# --- Cohesion: one architectural concern per file --------------------------- #


def test_cohesion_flags_model_and_route_in_one_file() -> None:
    rules = _py(
        "from pydantic import BaseModel\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "class UserIn(BaseModel):\n"
        "    name: str\n"
        "@router.post('/users')\n"
        "def create_user(u):\n"
        "    return u\n"
    )
    assert "modular_cohesion" in rules


def test_cohesion_clean_for_a_single_concern() -> None:
    rules = _py(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/a')\n"
        "def a():\n    return 1\n"
        "@router.get('/b')\n"
        "def b():\n    return 2\n"
    )
    assert "modular_cohesion" not in rules


# --- Thin routes: a route must delegate, not query the DB ------------------- #


def test_thin_routes_flags_db_access_in_route() -> None:
    rules = _py(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/users')\n"
        "def list_users(db):\n"
        "    return db.execute('select 1').scalars().all()\n"
    )
    assert "thin_routes" in rules


def test_thin_routes_clean_when_delegating_to_a_service() -> None:
    rules = _py(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/users')\n"
        "def list_users(svc):\n"
        "    return svc.list_users()\n"
    )
    assert "thin_routes" not in rules


def test_thin_routes_allows_explicit_commit_in_route() -> None:
    # Committing the unit of work after delegating is a common, valid pattern —
    # a bare `db.commit()` must not count as the route doing data access.
    rules = _py(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/users')\n"
        "async def create_user(svc, db):\n"
        "    user = await svc.create()\n"
        "    await db.commit()\n"
        "    return user\n"
    )
    assert "thin_routes" not in rules


# --- Thin components: data fetching belongs in a hook ----------------------- #


def test_thin_components_flags_fetch_in_component() -> None:
    rules = _ts(
        "export function UserList() {\n"
        "  const data = fetch('/api/users');\n"
        "  return <div>{data}</div>;\n"
        "}\n"
    )
    assert "thin_components" in rules


def test_thin_components_clean_when_presentational() -> None:
    rules = _ts(
        "export function UserList(props) {\n  return <ul>{props.users}</ul>;\n}\n"
    )
    assert "thin_components" not in rules


# --- God class: single responsibility --------------------------------------- #


def test_god_class_flags_a_class_with_too_many_methods() -> None:
    methods = "\n".join(f"    def m{i}(self):\n        return {i}" for i in range(16))
    assert "god_class" in _py("class Big:\n" + methods + "\n")


def test_god_class_clean_for_a_small_class() -> None:
    rules = _py("class Small:\n    def a(self):\n        return 1\n")
    assert "god_class" not in rules
