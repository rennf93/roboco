"""Modularity checks — separation of concerns the linters cannot see.

These are the senior-review judgements that ruff / eslint / mypy are blind to:
a file that mixes architectural concerns (a model defined inside a router), a
route handler that does its own data access instead of delegating to a service,
a React component that fetches data instead of using a hook, a class that has
grown into a god object. They inspect a file's COMPOSITION and a definition's
BODY, not just its top-level kind — which is what makes them about *quality*,
not lint.

Precision over recall: every check fires only on a confident, structural signal,
so a ``block``-level gate is never tripped by a guess. The rule names line up
with the per-project rule set so each can be levelled (warn/block) or waived.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .findings import Finding
from .grammars import get_parser

if TYPE_CHECKING:
    from tree_sitter import Node

    from roboco.foundation.policy.conventions.models import ConventionsStandard

    from .placement import Definition

# The architectural concerns whose co-location in one file is a modularity smell.
# A file should own a single one of these.
_CORE_KINDS = frozenset({"model", "route", "component"})

# A file may own at most this many distinct architectural concerns before it is
# a monolith that should be split.
_MAX_CONCERNS_PER_FILE = 1

# SQLAlchemy session methods + 2.0 constructs that signal data access. A route
# whose body calls one of these is doing a repository's / service's job.
# Transaction-lifecycle calls (commit / flush / refresh) are deliberately NOT
# here: a thin route legitimately commits the unit of work after delegating to a
# service — an explicit `await db.commit()` in the handler is a common pattern
# (e.g. when middleware-driven auto-commit is unreliable) and must not, on its
# own, count as the route doing data access.
_DB_METHODS = frozenset(
    {
        "execute",
        "scalar",
        "scalars",
        "stream",
        "stream_scalars",
        "add",
        "add_all",
        "merge",
        "query",
    }
)
# `add`/`add_all`/`merge` are ambiguous (set.add, cache.add, etc.) — only a DB
# hit when the receiver is a session handle. The other _DB_METHODS are unambiguous.
_AMBIGUOUS_DB_METHODS = frozenset({"add", "add_all", "merge"})
_SESSION_HANDLES = frozenset({"db", "session", "conn", "s"})

_DB_CONSTRUCTS = frozenset({"select", "insert", "update", "delete"})

# Data-fetching that belongs in a hook / query layer, not in a component body.
_FETCH_IDENTIFIERS = frozenset({"fetch", "axios"})

# A class with more methods than this is doing too much (single responsibility).
_GOD_CLASS_METHODS = 15

_ROUTER_OBJECTS = frozenset({"router", "app"})
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})

_JSX_TYPES = frozenset({"jsx_element", "jsx_self_closing_element", "jsx_fragment"})


def check_modularity(
    rel_path: str,
    defs: list[Definition],
    source: bytes,
    language: str,
    standard: ConventionsStandard,
) -> list[Finding]:
    """Return modularity findings for one changed file."""
    findings = _check_cohesion(rel_path, defs, standard)
    if language == "python":
        findings += _check_python_bodies(rel_path, source, standard)
    else:
        findings += _check_ts_bodies(rel_path, source, language, standard)
    return findings


def _level(standard: ConventionsStandard, rule: str, default: str) -> str:
    rule_obj = standard.rules.get(rule)
    return rule_obj.level if rule_obj is not None else default


# --------------------------------------------------------------------------- #
# Cohesion — one architectural concern per file
# --------------------------------------------------------------------------- #


def _check_cohesion(
    rel_path: str, defs: list[Definition], standard: ConventionsStandard
) -> list[Finding]:
    core = [(name, line, kind) for name, line, kind in defs if kind in _CORE_KINDS]
    kinds = {kind for _name, _line, kind in core}
    if len(kinds) <= _MAX_CONCERNS_PER_FILE:
        return []
    first_line = min(line for _name, line, _kind in core)
    return [
        Finding(
            file=rel_path,
            line=first_line,
            kind=None,
            rule="modular_cohesion",
            level=_level(standard, "modular_cohesion", "block"),
            message=(
                "this file mixes architectural concerns ("
                + ", ".join(sorted(kinds))
                + ") — each belongs in its own module"
            ),
            fix_hint=(
                "modularize: split this file so it owns a single concern — move "
                "models, routes, and components into separate modules"
            ),
        )
    ]


# --------------------------------------------------------------------------- #
# Python bodies — thin routes + god classes
# --------------------------------------------------------------------------- #


def _check_python_bodies(
    rel_path: str, source: bytes, standard: ConventionsStandard
) -> list[Finding]:
    root = get_parser("python").parse(source).root_node
    findings: list[Finding] = []
    for node in root.children:
        func, decorators = _py_function(node)
        if func is not None and _py_is_route(decorators) and _body_hits_db(func):
            findings.append(_thin_route_finding(rel_path, func, standard))
        cls = _py_class(node)
        if cls is not None and _py_method_count(cls) > _GOD_CLASS_METHODS:
            findings.append(_god_class_finding(rel_path, cls, standard))
    return findings


def _thin_route_finding(
    rel_path: str, func: Node, standard: ConventionsStandard
) -> Finding:
    name = _text(func.child_by_field_name("name"))
    return Finding(
        file=rel_path,
        line=func.start_point[0] + 1,
        kind="route",
        rule="thin_routes",
        level=_level(standard, "thin_routes", "block"),
        message=(
            f"route '{name}' performs its own data access — a route should "
            "delegate to a service / repository, not query the database"
        ),
        fix_hint=(
            "modularize: move the data-access logic into a service and call it "
            "from the route"
        ),
    )


def _god_class_finding(
    rel_path: str, cls: Node, standard: ConventionsStandard
) -> Finding:
    name = _text(cls.child_by_field_name("name"))
    return Finding(
        file=rel_path,
        line=cls.start_point[0] + 1,
        kind=None,
        rule="god_class",
        level=_level(standard, "god_class", "warn"),
        message=(
            f"class '{name}' has more than {_GOD_CLASS_METHODS} methods — it is "
            "likely doing too much (single-responsibility)"
        ),
        fix_hint="decompose: split the class along its distinct responsibilities",
    )


def _py_function(node: Node) -> tuple[Node | None, list[Node]]:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        decorators = [c for c in node.children if c.type == "decorator"]
        if inner is not None and inner.type == "function_definition":
            return inner, decorators
        return None, []
    if node.type == "function_definition":
        return node, []
    return None, []


def _py_class(node: Node) -> Node | None:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None and inner.type == "class_definition":
            return inner
        return None
    return node if node.type == "class_definition" else None


def _py_is_route(decorators: list[Node]) -> bool:
    return any(_py_decorator_is_route(d) for d in decorators)


def _py_decorator_is_route(decorator: Node) -> bool:
    expr = next((c for c in decorator.children if c.type != "@"), None)
    if expr is not None and expr.type == "call":
        expr = expr.child_by_field_name("function")
    if expr is None or expr.type != "attribute":
        return False
    obj = expr.child_by_field_name("object")
    method = _text(expr.child_by_field_name("attribute"))
    obj_name = _text(obj) if obj is not None and obj.type == "identifier" else ""
    return obj_name in _ROUTER_OBJECTS or method in _HTTP_METHODS


def _body_hits_db(func: Node) -> bool:
    body = func.child_by_field_name("body")
    if body is None:
        return False
    for call in _descendant_nodes(body, "call"):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "attribute":
            method = _text(fn.child_by_field_name("attribute"))
            if method in _DB_METHODS:
                if method in _AMBIGUOUS_DB_METHODS:
                    obj = fn.child_by_field_name("object")
                    obj_name = (
                        _text(obj)
                        if obj is not None and obj.type == "identifier"
                        else ""
                    )
                    if obj_name in _SESSION_HANDLES:
                        return True
                    # non-session receiver (seen_tags.add, cache.add) — not DB
                else:
                    return True
        elif fn.type == "identifier" and _text(fn) in _DB_CONSTRUCTS:
            return True
    return False


def _py_method_count(cls: Node) -> int:
    body = cls.child_by_field_name("body")
    if body is None:
        return 0
    count = 0
    for child in body.children:
        if child.type == "function_definition":
            count += 1
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is not None and inner.type == "function_definition":
                count += 1
    return count


# --------------------------------------------------------------------------- #
# TypeScript bodies — thin components
# --------------------------------------------------------------------------- #


def _check_ts_bodies(
    rel_path: str, source: bytes, language: str, standard: ConventionsStandard
) -> list[Finding]:
    parser_language = "tsx" if language in ("typescript", "tsx") else language
    root = get_parser(parser_language).parse(source).root_node
    findings: list[Finding] = []
    for func in _ts_component_functions(root):
        if _component_fetches(func):
            findings.append(_thin_component_finding(rel_path, func, standard))
    return findings


def _thin_component_finding(
    rel_path: str, func: Node, standard: ConventionsStandard
) -> Finding:
    name = _text(func.child_by_field_name("name")) or "component"
    return Finding(
        file=rel_path,
        line=func.start_point[0] + 1,
        kind="component",
        rule="thin_components",
        level=_level(standard, "thin_components", "block"),
        message=(
            f"component '{name}' fetches data in its body — data fetching "
            "belongs in a hook / query, not in a presentational component"
        ),
        fix_hint=(
            "modularize: extract the data fetching into a custom hook and "
            "consume its result from the component"
        ),
    )


def _ts_component_functions(root: Node) -> list[Node]:
    components: list[Node] = []
    for node in _descendant_nodes(root, "function_declaration"):
        if _contains_jsx(node):
            components.append(node)
    for node in _descendant_nodes(root, "arrow_function"):
        if _contains_jsx(node):
            components.append(node)
    return components


def _component_fetches(func: Node) -> bool:
    body = func.child_by_field_name("body")
    if body is None:
        return False
    for call in _descendant_nodes(body, "call_expression"):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier" and _text(fn) in _FETCH_IDENTIFIERS:
            return True
        if fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            if obj is not None and _text(obj) in _FETCH_IDENTIFIERS:
                return True
    return False


def _contains_jsx(node: Node) -> bool:
    body = node.child_by_field_name("body")
    return any(True for _ in _descendant_nodes(body, *_JSX_TYPES)) if body else False


# --------------------------------------------------------------------------- #
# Shared AST helpers
# --------------------------------------------------------------------------- #


def _descendant_nodes(node: Node | None, *types: str) -> list[Node]:
    if node is None:
        return []
    wanted = frozenset(types)
    out: list[Node] = []
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in wanted:
            out.append(current)
        stack.extend(current.children)
    return out


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode()
