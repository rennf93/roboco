"""Regenerate per-role verb tables from Pydantic schemas + role_config.

Audit P2-9 / D-04, D-10, D-11, D-29, D-30, D-31. Eliminates the
prompt-drift class: instead of curating verb tables in prose, derive
them from the same schemas the API enforces. When a schema changes,
re-run this script and the prompt-side tables update.

Output: ``agents/prompts/_generated/verbs.md`` — one section per role.
Role prompts reference this file's section instead of duplicating
verb signatures inline.

Usage:
    uv run python scripts/regenerate_verb_tables.py

The script reads from:
    - ``roboco.services.gateway.role_config.ROLE_CONFIGS`` for role -> verb list
    - ``roboco.api.schemas.v1.flow`` for flow verb body schemas
    - ``roboco.api.schemas.v1.do`` for content tool body schemas
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from roboco.api.schemas.v1 import do as do_schemas
from roboco.api.schemas.v1 import flow as flow_schemas
from roboco.services.gateway.role_config import ROLE_CONFIGS

_OUT_DIR = Path(__file__).resolve().parents[1] / "agents/prompts/_generated"
_OUT = _OUT_DIR / "verbs.md"

# Map verb name -> request schema class. Verbs without a schema (e.g. an
# empty-body endpoint) get an empty body description. Falls back via name
# similarity for verbs whose schema name diverges from the verb (e.g.
# ``pass`` is a Python keyword so the schema is ``PassReviewRequest``).
_VERB_TO_SCHEMA: dict[str, type[BaseModel]] = {
    # Flow — dev
    "give_me_work": flow_schemas.GiveMeWorkRequest,
    "i_will_work_on": flow_schemas.IWillWorkOnRequest,
    "open_pr": flow_schemas.OpenPrRequest,
    "i_am_done": flow_schemas.IAmDoneRequest,
    "i_am_blocked": flow_schemas.IAmBlockedRequest,
    "unclaim": flow_schemas.UnclaimRequest,
    "resume": flow_schemas.ResumeRequest,
    "i_am_idle": flow_schemas.IAmIdleRequest,
    # Flow — qa
    "claim_review": flow_schemas.ClaimReviewRequest,
    "pass": flow_schemas.PassReviewRequest,
    "fail": flow_schemas.FailReviewRequest,
    # Flow — doc
    "claim_doc_task": flow_schemas.ClaimDocTaskRequest,
    "i_documented": flow_schemas.IDocumentedRequest,
    # Flow — pm
    "triage": flow_schemas.TriageRequest,
    "triage_all": flow_schemas.TriageRequest,
    "unblock": flow_schemas.UnblockRequest,
    "complete": flow_schemas.CompleteRequest,
    "escalate_up": flow_schemas.EscalateUpRequest,
    "escalate_to_ceo": flow_schemas.EscalateToCeoRequest,
}


def _flow_extra_schemas() -> dict[str, type[BaseModel]]:
    """Pick up schemas defined in flow.py that aren't in the explicit map."""
    extras: dict[str, type[BaseModel]] = {}
    for name, obj in inspect.getmembers(flow_schemas, inspect.isclass):
        if not issubclass(obj, BaseModel) or obj is BaseModel:
            continue
        # Heuristic: ``IWillPlanRequest`` -> ``i_will_plan``.
        if not name.endswith("Request"):
            continue
        verb_camel = name[: -len("Request")]
        verb = _camel_to_snake(verb_camel)
        extras.setdefault(verb, obj)
    return extras


def _do_schemas() -> dict[str, type[BaseModel]]:
    """Schemas from do.py keyed by content-tool name."""
    out: dict[str, type[BaseModel]] = {}
    for name, obj in inspect.getmembers(do_schemas, inspect.isclass):
        if not issubclass(obj, BaseModel) or obj is BaseModel:
            continue
        if not name.endswith("Request"):
            continue
        verb_camel = name[: -len("Request")]
        verb = _camel_to_snake(verb_camel)
        out.setdefault(verb, obj)
    return out


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if i and ch.isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _format_field(name: str, field_info: Any) -> str:
    """Render one field as ``name: type [= default]``."""
    annot = field_info.annotation
    type_str = _annot_str(annot)
    if field_info.is_required():
        return f"{name}: {type_str}"
    default = field_info.default
    return f"{name}: {type_str} = {default!r}"


def _annot_str(annot: Any) -> str:
    if annot is None or annot is type(None):
        return "None"
    origin = get_origin(annot)
    if origin is None:
        return getattr(annot, "__name__", str(annot))
    args = get_args(annot)
    if origin in (list,):
        inner = ", ".join(_annot_str(a) for a in args)
        return f"list[{inner}]"
    # Optional / Union
    rendered = " | ".join(_annot_str(a) for a in args)
    return rendered


def _signature_for_schema(schema: type[BaseModel]) -> str:
    """Render the schema's required + optional fields as a Python-like signature."""
    fields = schema.model_fields
    if not fields:
        return "()"
    parts = [_format_field(name, info) for name, info in fields.items()]
    return "(" + ", ".join(parts) + ")"


def _render_role_section(role: str) -> str:
    cfg = ROLE_CONFIGS[role]
    flow_schemas_map = {**_VERB_TO_SCHEMA, **_flow_extra_schemas()}
    do_schemas_map = _do_schemas()

    lines: list[str] = [f"## {role}", ""]
    lines.append("### Flow verbs")
    lines.append("")
    lines.append("| Verb | Body schema |")
    lines.append("|------|-------------|")
    for verb in cfg.flow_tools:
        schema = flow_schemas_map.get(verb)
        sig = (
            _signature_for_schema(schema)
            if schema is not None
            else "(unknown — no Pydantic schema)"
        )
        lines.append(f"| `{verb}` | `{verb}{sig}` |")
    lines.append("")
    lines.append("### Content (do) tools")
    lines.append("")
    lines.append("| Tool | Body schema |")
    lines.append("|------|-------------|")
    for tool in cfg.do_tools:
        schema = do_schemas_map.get(tool)
        sig = _signature_for_schema(schema) if schema is not None else "(see do_server)"
        lines.append(f"| `{tool}` | `{tool}{sig}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    sections = [
        "<!-- AUTOGENERATED by scripts/regenerate_verb_tables.py. -->",
        "<!-- Source: services/gateway/role_config.py + api/schemas/v1/. -->",
        "",
        "# Per-role verb shapes (autogenerated)",
        "",
        "Run `uv run python scripts/regenerate_verb_tables.py` after changing",
        "any role config or schema. Role prompts reference this file's sections",
        "as the source of truth for verb signatures.",
        "",
    ]
    for role in ROLE_CONFIGS:
        sections.append(_render_role_section(role))

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(sections) + "\n")
    print(f"wrote {_OUT.relative_to(_OUT.parents[2])}")

    # Also write per-role files so the prompt composer can include each
    # role's verb table directly without parsing a multi-section document.
    for role in ROLE_CONFIGS:
        per_role = _OUT_DIR / f"{role}.md"
        header = (
            "<!-- AUTOGENERATED by scripts/regenerate_verb_tables.py. -->\n"
            "<!-- Per-role verb signatures, derived from Pydantic schemas. -->\n\n"
            "## Verbs available to you (autogenerated source of truth)\n\n"
        )
        body = _render_role_section(role)
        # Strip the leading `## <role>` header — it's redundant once the
        # composer injects this under a role prompt that already names the
        # role in its identity section.
        body_lines = body.splitlines()
        if body_lines and body_lines[0].startswith("## "):
            body_lines = body_lines[1:]
            while body_lines and not body_lines[0].strip():
                body_lines = body_lines[1:]
        per_role.write_text(header + "\n".join(body_lines) + "\n")
        print(f"wrote {per_role.relative_to(_OUT.parents[2])}")


if __name__ == "__main__":
    main()
