"""Classify each top-level Python definition into an architectural *kind*.

Precision over recall: a definition that is not clearly a model, route, or
helper abstains to ``other`` (no finding) so a ``block`` gate can never strand
a task on a false positive. Only module-level definitions are considered.

- ``model``   — a class extending ``BaseModel`` / ``DeclarativeBase`` / ``Base``.
- ``route``   — a function decorated with ``@router.*`` / ``@app.*`` or any
  ``@<x>.get|post|put|delete|patch``.
- ``helper``  — any other module-level function.
- ``other``   — anything ambiguous (abstain).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.foundation.policy.conventions.models import DefinitionKind

from .grammars import get_parser

if TYPE_CHECKING:
    from tree_sitter import Node

Definition = tuple[str, int, DefinitionKind]

_MODEL_BASES = frozenset({"BaseModel", "DeclarativeBase", "Base"})
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_ROUTER_OBJECTS = frozenset({"router", "app"})


def classify_definitions(source: bytes) -> list[Definition]:
    """Return ``(name, line, kind)`` for each top-level definition in ``source``."""
    root = get_parser("python").parse(source).root_node
    results: list[Definition] = []
    for node in root.children:
        classified = _classify_top_level(node)
        if classified is not None:
            results.append(classified)
    return results


def _classify_top_level(node: Node) -> Definition | None:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        decorators = [c for c in node.children if c.type == "decorator"]
        return _classify_def(inner, decorators) if inner is not None else None
    if node.type in ("class_definition", "function_definition"):
        return _classify_def(node, [])
    return None


def _classify_def(node: Node, decorators: list[Node]) -> Definition | None:
    name = _text(node.child_by_field_name("name"))
    if not name:
        return None
    line = node.start_point[0] + 1
    if node.type == "class_definition":
        kind: DefinitionKind = "model" if _is_model_class(node) else "other"
    elif _is_route(decorators):
        kind = "route"
    else:
        kind = "helper"
    return (name, line, kind)


def _is_model_class(class_node: Node) -> bool:
    supers = class_node.child_by_field_name("superclasses")
    if supers is None:
        return False
    return any(_base_name(child) in _MODEL_BASES for child in supers.children)


def _base_name(node: Node) -> str:
    if node.type == "identifier":
        return _text(node)
    if node.type == "attribute":
        return _text(node.child_by_field_name("attribute"))
    return ""


def _is_route(decorators: list[Node]) -> bool:
    return any(_decorator_is_route(d) for d in decorators)


def _decorator_is_route(decorator: Node) -> bool:
    expr = next((c for c in decorator.children if c.type != "@"), None)
    if expr is not None and expr.type == "call":
        expr = expr.child_by_field_name("function")
    if expr is None or expr.type != "attribute":
        return False
    obj = expr.child_by_field_name("object")
    method = _text(expr.child_by_field_name("attribute"))
    obj_name = _base_name(obj) if obj is not None else ""
    return obj_name in _ROUTER_OBJECTS or method in _HTTP_METHODS


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode()
