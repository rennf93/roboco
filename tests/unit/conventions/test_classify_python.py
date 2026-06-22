"""Python definition-kind classification (tree-sitter), precision-over-recall."""

from __future__ import annotations

from roboco.conventions.classify_python import classify_definitions


def test_pydantic_model_is_classified_model() -> None:
    src = b"from pydantic import BaseModel\nclass UserCreate(BaseModel):\n    x: int\n"
    defs = classify_definitions(src)
    assert ("UserCreate", 2, "model") in defs


def test_dotted_base_model_is_classified_model() -> None:
    defs = classify_definitions(b"class M(pydantic.BaseModel):\n    pass\n")
    assert defs == [("M", 1, "model")]


def test_sqlalchemy_declarative_base_is_model() -> None:
    defs = classify_definitions(b"class Account(Base):\n    pass\n")
    assert defs == [("Account", 1, "model")]


def test_router_decorated_function_is_route() -> None:
    src = b"@router.get('/x')\ndef list_x():\n    return 1\n"
    defs = classify_definitions(src)
    assert defs == [("list_x", 2, "route")]


def test_app_post_decorated_function_is_route() -> None:
    src = b"@app.post('/y')\ndef create_y():\n    return 1\n"
    assert classify_definitions(src) == [("create_y", 2, "route")]


def test_blueprint_get_decorated_function_is_route() -> None:
    # Any object with an HTTP-method attribute counts as a route handler.
    src = b"@bp.delete('/z')\ndef drop_z():\n    return 1\n"
    assert classify_definitions(src) == [("drop_z", 2, "route")]


def test_plain_function_is_helper() -> None:
    assert classify_definitions(b"def helper():\n    pass\n") == [
        ("helper", 1, "helper")
    ]


def test_non_route_decorated_function_is_helper() -> None:
    # A decorator that is not an HTTP route still leaves a plain function.
    src = b"@functools.cache\ndef compute():\n    return 1\n"
    assert classify_definitions(src) == [("compute", 2, "helper")]


def test_ambiguous_class_abstains_to_other() -> None:
    assert classify_definitions(b"class Thing:\n    pass\n") == [("Thing", 1, "other")]


def test_class_with_unknown_base_abstains() -> None:
    assert classify_definitions(b"class Widget(Gadget):\n    pass\n") == [
        ("Widget", 1, "other")
    ]


def test_multiple_top_level_defs_in_order() -> None:
    src = (
        b"from pydantic import BaseModel\n"
        b"class Req(BaseModel):\n    x: int\n"
        b"@router.put('/u')\ndef upd():\n    return 1\n"
        b"def util():\n    pass\n"
    )
    defs = classify_definitions(src)
    assert defs == [
        ("Req", 2, "model"),
        ("upd", 5, "route"),
        ("util", 7, "helper"),
    ]


def test_nested_defs_are_not_top_level() -> None:
    # Only module-level definitions are classified (precision).
    src = b"def outer():\n    def inner():\n        pass\n    return inner\n"
    assert classify_definitions(src) == [("outer", 1, "helper")]
