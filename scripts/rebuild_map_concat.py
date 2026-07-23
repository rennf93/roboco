"""Rebuild docs/map/_complete_map.md from _front.md + the per-slice maps.

The complete map is a literal concatenation: the front matter, then each
slice's body (everything from its "## Purpose" heading on — any per-file
title block above it is stripped) in the canonical order below, one blank
line between sections. It has drifted behind its own sources by hand-sync
three times; this script makes the rebuild mechanical.

Usage:
    python scripts/rebuild_map_concat.py           # rewrite the concat
    python scripts/rebuild_map_concat.py --check   # exit 1 if drifted
"""

from __future__ import annotations

import sys
from pathlib import Path

MAP_DIR = Path(__file__).resolve().parent.parent / "docs" / "map"

# The committed artifact's own section order (recovered from the file; not
# alphabetical and not the ToC order — keep appending new slices here).
SLICE_ORDER = [
    "a2a-audit-journal-permissions",
    "deployment-tooling",
    "engines-heal-ciwatch-depupdate",
    "foundation-batch-sequencing",
    "foundation-conventions-identity",
    "foundation-lifecycle",
    "foundation-policy-misc",
    "gateway-support",
    "intake-secretary",
    "notification",
    "orchestrator",
    "org-memory-playbooks",
    "pr-gate-review",
    "prompts-roles-taxonomy",
    "release-manager",
    "runtime-providers",
    "vault",
    "video-engine",
    "models",
    "db-migrations",
    "api-core-websocket",
    "api-routes-schemas",
    "mcp-servers",
    "choreographer",
    "task-service",
    "worksession-git",
    "workspace",
    "support-services",
    "review-findings",
    "metrics-observability",
    "conventions-service-validator",
    "product-strategy-research-pitch",
    "engine-docs-sync",
    "panel",
    "tests",
]


def _slice_body(path: Path) -> str:
    text = path.read_text()
    idx = text.find("## Purpose")
    if idx < 0:
        raise SystemExit(f"{path.name}: no '## Purpose' heading — cannot concat")
    return text[idx:].rstrip("\n") + "\n"


def build() -> str:
    on_disk = {p.stem for p in MAP_DIR.glob("*.md") if not p.name.startswith("_")}
    listed = set(SLICE_ORDER)
    if on_disk != listed:
        raise SystemExit(
            "slice set drifted — missing from SLICE_ORDER: "
            f"{sorted(on_disk - listed)}, listed but absent: {sorted(listed - on_disk)}"
        )
    front = (MAP_DIR / "_front.md").read_text()
    return front + "".join(
        "\n" + _slice_body(MAP_DIR / f"{name}.md") for name in SLICE_ORDER
    )


def main() -> int:
    target = MAP_DIR / "_complete_map.md"
    built = build()
    if "--check" in sys.argv[1:]:
        if target.read_text() != built:
            print(
                "_complete_map.md is behind its sources — "
                "run scripts/rebuild_map_concat.py"
            )
            return 1
        print("_complete_map.md is in sync.")
        return 0
    target.write_text(built)
    print(f"wrote {target} ({len(built)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
