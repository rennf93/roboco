"""A single convention finding — the validator's unit of output.

One ``Finding`` per violation, serialized as one JSON line by the CLI. The
gateway gates parse these lines and block on any ``level == "block"`` finding.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Finding:
    """One placement / hygiene / custom-rule violation on a changed file."""

    file: str
    line: int
    kind: str | None
    rule: str
    level: str
    message: str
    fix_hint: str

    def as_json(self) -> str:
        """Render the finding as a single compact JSON object (one line)."""
        return json.dumps(asdict(self), separators=(",", ":"))
