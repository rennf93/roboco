"""TypeScript / TSX definition-kind classification, precision-over-recall."""

from __future__ import annotations

from roboco.conventions.classify_ts import classify_definitions


def test_zod_schema_const_is_model() -> None:
    src = b"export const UserSchema = z.object({ id: z.string() });\n"
    assert ("UserSchema", 1, "model") in classify_definitions(src, "typescript")


def test_chained_zod_schema_is_model() -> None:
    src = b"export const P = z.object({}).partial();\n"
    assert ("P", 1, "model") in classify_definitions(src, "typescript")


def test_entity_class_is_model() -> None:
    src = b"@Entity()\nexport class User {}\n"
    assert ("User", 2, "model") in classify_definitions(src, "typescript")


def test_controller_class_is_route() -> None:
    src = b"@Controller('users')\nexport class UsersController {}\n"
    assert ("UsersController", 2, "route") in classify_definitions(src, "typescript")


def test_arrow_component_is_component() -> None:
    src = b"export const Btn = () => <div/>;\n"
    assert ("Btn", 1, "component") in classify_definitions(src, "tsx")


def test_function_component_is_component() -> None:
    src = b"export function Card() { return <span/>; }\n"
    assert ("Card", 1, "component") in classify_definitions(src, "tsx")


def test_plain_function_abstains_to_other() -> None:
    src = b"export function add(a: number, b: number) { return a + b; }\n"
    assert classify_definitions(src, "typescript") == [("add", 1, "other")]


def test_plain_const_abstains_to_other() -> None:
    src = b"export const TAX = 0.2;\n"
    assert classify_definitions(src, "typescript") == [("TAX", 1, "other")]


def test_unparseable_source_abstains_quietly() -> None:
    # tree-sitter yields ERROR nodes; we must not crash or invent findings.
    assert classify_definitions(b"export const = = =;\n", "typescript") == []
