"""Architectural-conventions standard — schema models + YAML parse.

The standard is the repo-canonical ``.roboco/conventions.yml``: a per-project
architecture map (which definition *kinds* belong in which modules), a
toggleable rule set, custom regex rules, and waivers. These models are pure
(no IO, no DB) — the validator, the service, and the effective-map merge all
build on them. ``parse_yaml`` is the single entry point from raw file text to
a validated ``ConventionsStandard`` (or a ``ConventionsParseError``).
"""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

RuleLevel = Literal["warn", "block"]
DefinitionKind = Literal[
    "model", "route", "helper", "business_logic", "component", "other"
]


class ConventionsParseError(ValueError):
    """Raised when ``.roboco/conventions.yml`` is malformed or invalid."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# The org-default rule set: applied to every project's effective map before the
# committed file or auto-derived rules overlay it. Keep in sync with the
# validator's rule emitters and the panel's rule list.
BUILTIN_RULES: dict[str, RuleLevel] = {
    "no_models_in_routers": "block",
    "no_helpers_in_routers": "block",
    "no_lint_suppressions": "block",
    "no_inline_comments": "warn",
}


class _Base(BaseModel):
    """Shared config: ignore unknown keys for forward-compatibility."""

    model_config = ConfigDict(extra="ignore")


class Module(_Base):
    """One module boundary: a path prefix, its purpose, forbidden def kinds."""

    path: str
    purpose: str
    forbidden: list[DefinitionKind] = Field(default_factory=list)


class Rule(_Base):
    """A toggleable rule — its name and the level it fires at."""

    name: str
    level: RuleLevel


class CustomRule(_Base):
    """A project-specific regex rule, optionally scoped to languages."""

    id: str
    pattern: str
    message: str
    level: RuleLevel
    languages: list[str] = Field(default_factory=list)


class Waiver(_Base):
    """An accountable escape hatch: a (path, rule) the gate must not flag."""

    path: str
    rule: str
    reason: str


class ConventionsStandard(_Base):
    """The parsed standard (raw file *or* the merged effective map)."""

    version: int = 1
    languages: list[str] = Field(default_factory=list)
    modules: list[Module] = Field(default_factory=list)
    rules: dict[str, Rule] = Field(default_factory=dict)
    custom: list[CustomRule] = Field(default_factory=list)
    waivers: list[Waiver] = Field(default_factory=list)

    @field_validator("rules", mode="before")
    @classmethod
    def _name_rules_from_keys(cls, v: Any) -> Any:
        """Inject the mapping key as each rule's ``name``.

        The YAML keys rules by name with a ``{level: ...}`` value; the model
        carries the name on the rule itself. Accept either shape so a ``Rule``
        constructed directly also passes through unchanged.
        """
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for name, spec in v.items():
            if isinstance(spec, dict):
                out[name] = {"name": name, **spec}
            else:
                out[name] = spec
        return out

    @classmethod
    def parse_yaml(cls, text: str) -> ConventionsStandard:
        """Parse ``.roboco/conventions.yml`` text into a validated standard.

        Raises ``ConventionsParseError`` on malformed YAML, a non-mapping
        top level, or any schema violation (e.g. an unknown rule level).
        """
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConventionsParseError(f"malformed YAML: {exc}") from exc
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConventionsParseError("top-level conventions must be a mapping")
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConventionsParseError(str(exc)) from exc
