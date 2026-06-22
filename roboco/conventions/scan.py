"""Auto-derive a conventions standard from a repo, and render it to YAML.

Pure (filesystem read only). ``derive_from_scan`` infers module boundaries
from directory names, detects languages from file extensions, seeds the org
``BUILTIN_RULES``, and best-effort lifts imperative ``CLAUDE.md`` lines that
name a concrete token into warn-level custom rules. ``render_yaml`` emits a
commented, human-friendly file that round-trips through ``parse_yaml``.

Lives in the validator package (not ``services/``) so the lightweight
``roboco.conventions`` CLI can import it without pulling DB-backed services.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from roboco.foundation.policy.conventions.models import (
    BUILTIN_RULES,
    ConventionsStandard,
    CustomRule,
    DefinitionKind,
    Module,
    Rule,
    RuleLevel,
)

_IGNORE_DIRS = frozenset(
    {
        "node_modules",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "site-packages",
        "target",
        "vendor",
    }
)

# Directory-name keywords -> (purpose, forbidden definition kinds). Only kinds
# the classifiers actually emit can ever fire, so extra entries are harmless.
_MODULE_PATTERNS: tuple[tuple[frozenset[str], str, tuple[DefinitionKind, ...]], ...] = (
    (
        frozenset({"routers", "routes", "api", "endpoints", "controllers"}),
        "HTTP routes / endpoint definitions",
        ("model", "helper"),
    ),
    (
        frozenset({"models", "schemas", "entities", "dto", "dtos"}),
        "data models / schemas",
        ("route",),
    ),
    (
        frozenset({"services", "domain", "usecases"}),
        "business logic & orchestration",
        ("route",),
    ),
    (
        frozenset({"components"}),
        "UI components",
        ("model", "route"),
    ),
    (
        frozenset({"hooks"}),
        "React hooks",
        ("component", "model", "route"),
    ),
    (
        frozenset({"store", "stores", "state", "context", "contexts"}),
        "state management",
        ("component", "route"),
    ),
    (
        frozenset({"helpers", "utils", "util", "lib"}),
        "shared helpers / utilities",
        ("route", "component"),
    ),
)

_LANGUAGE_BY_SUFFIX = {".py": "python", ".ts": "typescript", ".tsx": "typescript"}
_IMPERATIVE = re.compile(r"\b(never|don't|do not|avoid|no)\b", re.IGNORECASE)
_CODE_SPAN = re.compile(r"`([^`]+)`")
_MAX_LIFTED_RULES = 25


def derive_from_scan(root: Path | str) -> ConventionsStandard:
    """Infer a conventions standard from the repository at ``root``."""
    root_path = Path(root)
    modules = _scan_modules(root_path)
    languages = _detect_languages(root_path)
    return ConventionsStandard(
        languages=languages,
        modules=modules,
        rules=_seed_rules(modules, languages),
        custom=_lift_claude_md(root_path),
    )


# Placement rules default to block — the level placement.py applies when a rule
# is absent — so auto-derived enforcement has teeth; the owner downgrades any
# rule to warn per-rule via the panel editor or the committed file.
_PLACEMENT_DEFAULT: RuleLevel = "block"

# Modularity rules — the separation-of-concerns checks that go beyond linting.
# Cohesion + god-class apply to any classifiable project; the body checks are
# stack-specific (thin routes for Python APIs, thin components for TS/React), so
# a Python project never carries thin_components and a frontend never carries
# thin_routes.
_MODULARITY_ANY: dict[str, RuleLevel] = {
    "modular_cohesion": "block",
    "god_class": "warn",
}
_MODULARITY_BY_LANGUAGE: dict[str, dict[str, RuleLevel]] = {
    "python": {"thin_routes": "block"},
    "typescript": {"thin_components": "block"},
}


def _seed_rules(modules: list[Module], languages: list[str]) -> dict[str, Rule]:
    """Seed the rules that actually apply to this project.

    Three layers, each scoped so a project never carries a rule that cannot fire
    on it:

    - **Hygiene** (``BUILTIN_RULES``) — language-agnostic, seeded for everyone.
    - **Placement** — seeded per detected module; the rule name mirrors the
      validator's ``no_<kind>s_in_<leaf>``, so a frontend repo gets
      ``no_models_in_components`` rather than a backend ``no_models_in_routers``.
    - **Modularity** — the separation-of-concerns checks; cohesion + god-class
      for any stack, plus the stack-specific body checks (thin routes for
      Python, thin components for TypeScript).
    """
    rules = {
        name: Rule(name=name, level=level) for name, level in BUILTIN_RULES.items()
    }
    for module in modules:
        leaf = module.path.rstrip("/").rsplit("/", 1)[-1]
        for kind in module.forbidden:
            name = f"no_{kind}s_in_{leaf}"
            rules.setdefault(name, Rule(name=name, level=_PLACEMENT_DEFAULT))
    if languages:
        for name, any_level in _MODULARITY_ANY.items():
            rules.setdefault(name, Rule(name=name, level=any_level))
    for language in languages:
        for name, lang_level in _MODULARITY_BY_LANGUAGE.get(language, {}).items():
            rules.setdefault(name, Rule(name=name, level=lang_level))
    return rules


def _walk_dirs(root: Path) -> list[tuple[str, list[str], list[str]]]:
    walked: list[tuple[str, list[str], list[str]]] = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")
        )
        walked.append((dirpath, dirnames, sorted(files)))
    return walked


def _scan_modules(root: Path) -> list[Module]:
    modules: list[Module] = []
    for dirpath, dirnames, _files in _walk_dirs(root):
        for name in dirnames:
            spec = _match_module(name)
            if spec is None:
                continue
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            purpose, forbidden = spec
            modules.append(Module(path=rel, purpose=purpose, forbidden=list(forbidden)))
    return modules


def _match_module(name: str) -> tuple[str, tuple[DefinitionKind, ...]] | None:
    lname = name.lower()
    for keywords, purpose, forbidden in _MODULE_PATTERNS:
        if lname in keywords:
            return purpose, forbidden
    return None


def _detect_languages(root: Path) -> list[str]:
    languages: list[str] = []
    for _dirpath, _dirnames, files in _walk_dirs(root):
        for filename in files:
            language = _LANGUAGE_BY_SUFFIX.get(Path(filename).suffix)
            if language is not None and language not in languages:
                languages.append(language)
    return languages


def _lift_claude_md(root: Path) -> list[CustomRule]:
    path = root / "CLAUDE.md"
    if not path.is_file():
        return []
    rules: list[CustomRule] = []
    seen: set[str] = set()
    for line in path.read_text(errors="replace").splitlines():
        rule = _rule_from_line(line, seen)
        if rule is not None:
            rules.append(rule)
        if len(rules) >= _MAX_LIFTED_RULES:
            break
    return rules


def _rule_from_line(line: str, seen: set[str]) -> CustomRule | None:
    if not _IMPERATIVE.search(line):
        return None
    span = _CODE_SPAN.search(line)
    if span is None:
        return None
    token = span.group(1).strip()
    rule_id = _slug(token)
    if not token or not rule_id or rule_id in seen:
        return None
    seen.add(rule_id)
    return CustomRule(
        id=rule_id,
        pattern=re.escape(token),
        message=line.strip().lstrip("-*# ").strip(),
        level="warn",
    )


def _slug(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", token.lower()).strip("-")


def _to_yaml_data(standard: ConventionsStandard) -> dict[str, object]:
    return {
        "version": standard.version,
        "languages": list(standard.languages),
        "modules": [
            {"path": m.path, "purpose": m.purpose, "forbidden": list(m.forbidden)}
            for m in standard.modules
        ],
        "rules": {name: {"level": r.level} for name, r in standard.rules.items()},
        "custom": [
            {
                "id": c.id,
                "pattern": c.pattern,
                "message": c.message,
                "level": c.level,
                "languages": list(c.languages),
            }
            for c in standard.custom
        ],
        "waivers": [
            {"path": w.path, "rule": w.rule, "reason": w.reason}
            for w in standard.waivers
        ],
    }


def render_yaml(standard: ConventionsStandard) -> str:
    """Render ``standard`` to a commented ``.roboco/conventions.yml`` string."""
    header = (
        "# Architectural conventions for this project.\n"
        "# Auto-scaffolded by RoboCo — edit freely; this file is canonical.\n"
        "# Each module lists definition KINDS forbidden in it; rules toggle\n"
        "# warn/block; waivers are accountable, PR-reviewed escape hatches.\n"
    )
    body = yaml.safe_dump(
        _to_yaml_data(standard),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return header + body
