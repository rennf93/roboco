"""Effective-map merge: auto-derived defaults overlaid by the committed file.

Every consumer (validator, ambient injection, baseline constraints) reads the
*effective* map, so behaviour is identical whether the file is present,
absent, or partial. Precedence, per field:

- ``rules``: ``BUILTIN_RULES`` < derived < file (per key).
- ``modules``: derived, with file modules overriding by ``path`` (and new
  paths appended in file order).
- ``custom`` / ``waivers`` / ``version``: the file's when a file is present,
  else the derived value (the file is the curated replacement).
- ``languages``: union (derived order, then file-only extras).

Pure: no IO, no DB.
"""

from __future__ import annotations

from .models import BUILTIN_RULES, ConventionsStandard, Module, Rule


def _merge_rules(
    derived: ConventionsStandard, file: ConventionsStandard | None
) -> dict[str, Rule]:
    merged: dict[str, Rule] = {
        name: Rule(name=name, level=level) for name, level in BUILTIN_RULES.items()
    }
    merged.update(derived.rules)
    if file is not None:
        merged.update(file.rules)
    return merged


def _merge_modules(
    derived: ConventionsStandard, file: ConventionsStandard | None
) -> list[Module]:
    modules = {m.path: m for m in derived.modules}
    for m in file.modules if file is not None else []:
        modules[m.path] = m
    return list(modules.values())


def _union_languages(
    derived: ConventionsStandard, file: ConventionsStandard | None
) -> list[str]:
    languages = list(derived.languages)
    for lang in file.languages if file is not None else []:
        if lang not in languages:
            languages.append(lang)
    return languages


def effective_map(
    derived: ConventionsStandard, file: ConventionsStandard | None
) -> ConventionsStandard:
    """Merge auto-derived defaults with the committed file into one standard."""
    curated = file if file is not None else derived
    return ConventionsStandard(
        version=curated.version,
        languages=_union_languages(derived, file),
        modules=_merge_modules(derived, file),
        rules=_merge_rules(derived, file),
        custom=curated.custom,
        waivers=curated.waivers,
    )
