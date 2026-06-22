"""Classify each top-level TypeScript / TSX definition into a *kind*.

Precision over recall: anything not clearly a model, component, or route
abstains to ``other``. Language must be ``typescript`` (``.ts``) or ``tsx``
(``.tsx``) — JSX only parses under the tsx grammar.

- ``model``     — a class with ``@Entity``/``@Schema``/``@Table`` (etc.), or a
  ``z.*`` zod-schema const.
- ``route``     — a class with ``@Controller`` or an HTTP-method decorator.
- ``component`` — an exported function / arrow that contains JSX.
- ``other``     — anything ambiguous (abstain).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.foundation.policy.conventions.models import DefinitionKind

from .grammars import get_parser

if TYPE_CHECKING:
    from tree_sitter import Node

Definition = tuple[str, int, DefinitionKind]

_MODEL_DECORATORS = frozenset({"Entity", "Schema", "Table", "ObjectType", "InputType"})
_ROUTE_DECORATORS = frozenset(
    {"Controller", "Get", "Post", "Put", "Delete", "Patch", "All"}
)
_CLASS_TYPES = frozenset({"class_declaration", "abstract_class_declaration"})
_DECL_TYPES = frozenset({"lexical_declaration", "function_declaration"} | _CLASS_TYPES)
_JSX_TYPES = frozenset({"jsx_element", "jsx_self_closing_element", "jsx_fragment"})


def classify_definitions(source: bytes, language: str = "tsx") -> list[Definition]:
    """Return ``(name, line, kind)`` for each top-level definition in ``source``."""
    root = get_parser(language).parse(source).root_node
    results: list[Definition] = []
    pending: list[str] = []
    for node in root.children:
        if node.type == "decorator":
            pending.append(_decorator_name(node))
            continue
        results.extend(_classify_top_level(node, pending))
        pending = []
    return results


def _classify_top_level(node: Node, pending: list[str]) -> list[Definition]:
    if node.type == "export_statement":
        decl = _inner_declaration(node)
        decorators = pending + _decorator_names(node)
        return _classify_decl(decl, decorators) if decl is not None else []
    if node.type in _DECL_TYPES:
        return _classify_decl(node, list(pending))
    return []


def _classify_decl(decl: Node, decorators: list[str]) -> list[Definition]:
    if decl.type in _CLASS_TYPES:
        return _classify_class(decl, decorators)
    if decl.type == "function_declaration":
        return _classify_function(decl)
    if decl.type == "lexical_declaration":
        return _classify_lexical(decl)
    return []


def _classify_class(decl: Node, decorators: list[str]) -> list[Definition]:
    name = _text(decl.child_by_field_name("name"))
    if not name:
        return []
    line = decl.start_point[0] + 1
    if any(d in _MODEL_DECORATORS for d in decorators):
        return [(name, line, "model")]
    if any(d in _ROUTE_DECORATORS for d in decorators):
        return [(name, line, "route")]
    return [(name, line, "other")]


def _classify_function(decl: Node) -> list[Definition]:
    name = _text(decl.child_by_field_name("name"))
    if not name:
        return []
    kind: DefinitionKind = "component" if _contains_jsx(decl) else "other"
    return [(name, decl.start_point[0] + 1, kind)]


def _classify_lexical(decl: Node) -> list[Definition]:
    out: list[Definition] = []
    for declarator in decl.children:
        if declarator.type != "variable_declarator":
            continue
        name = _text(declarator.child_by_field_name("name"))
        value = declarator.child_by_field_name("value")
        if not name or value is None:
            continue
        out.append((name, declarator.start_point[0] + 1, _lexical_kind(value)))
    return out


def _lexical_kind(value: Node) -> DefinitionKind:
    if _is_zod_schema(value):
        return "model"
    if value.type == "arrow_function" and _contains_jsx(value):
        return "component"
    return "other"


def _is_zod_schema(value: Node) -> bool:
    node: Node | None = value
    for _ in range(10):
        if node is None:
            return False
        if node.type == "call_expression":
            node = node.child_by_field_name("function")
        elif node.type == "member_expression":
            node = node.child_by_field_name("object")
        else:
            break
    return node is not None and node.type == "identifier" and _text(node) == "z"


def _contains_jsx(node: Node) -> bool:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in _JSX_TYPES:
            return True
        stack.extend(current.children)
    return False


def _decorator_names(node: Node) -> list[str]:
    return [_decorator_name(c) for c in node.children if c.type == "decorator"]


def _decorator_name(decorator: Node) -> str:
    expr = next((c for c in decorator.children if c.type != "@"), None)
    if expr is not None and expr.type == "call_expression":
        expr = expr.child_by_field_name("function")
    if expr is None:
        return ""
    if expr.type == "identifier":
        return _text(expr)
    if expr.type == "member_expression":
        return _text(expr.child_by_field_name("property"))
    return ""


def _inner_declaration(export_node: Node) -> Node | None:
    return next((c for c in export_node.children if c.type in _DECL_TYPES), None)


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode()
