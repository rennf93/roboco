"""regenerate_verb_tables._annot_str strips Annotated metadata (#199).

The flow schemas type coercion-hardened list fields as
``StrList = Annotated[list[str], BeforeValidator(coerce_str_list)]``. The old
``_annot_str`` walked ``get_args(Annotated[list[str], BeforeValidator(...)])``
and rendered the ``BeforeValidator(...)`` repr into the generated
``verbs.md`` / per-role prompts — leaking an internal validator object (with
a memory address) into agent-facing prompt text. The fix unwraps
``Annotated[T, ...]`` to ``T`` before rendering.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, BeforeValidator, Field

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "regenerate_verb_tables.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_regen_verb_tables", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _coerce(x: object) -> list[str]:
    return [str(x)]


_StrList = Annotated[list[str], BeforeValidator(_coerce)]


def test_annot_str_strips_before_validator_metadata() -> None:
    mod = _load_module()
    assert mod._annot_str(_StrList) == "list[str]"


def test_annot_str_strips_metadata_in_optional_union() -> None:
    # The schemas use ``StrList | None``; the metadata must drop on both arms.
    mod = _load_module()
    assert mod._annot_str(_StrList | None) == "list[str] | None"


def test_annot_str_plain_types_unchanged() -> None:
    mod = _load_module()
    assert mod._annot_str(list[str]) == "list[str]"
    assert mod._annot_str(str | None) == "str | None"
    assert mod._annot_str(None) == "None"


def test_signature_for_schema_has_no_beforevalidator_repr() -> None:
    # End-to-end: a schema with a StrList | None field renders a clean
    # signature, no ``BeforeValidator(func=...)`` leak.
    class _Sample(BaseModel):
        ac_verdicts: _StrList | None = Field(default=None)
        notes: str

    mod = _load_module()
    sig = mod._signature_for_schema(_Sample)
    assert "BeforeValidator" not in sig
    assert "list[str] | None" in sig
