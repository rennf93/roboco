#!/usr/bin/env python3
"""Generate GitHub Pages redirect stubs for every page MkDocs used to publish.

Docs-site split Phase 1 (docs/internal/specs/2026-07-03-docs-site-split.md):
docs.roboco.tech (the `roboco-website` repo) is now the canonical user-facing
docs site; this repo's MkDocs build retires. Every URL MkDocs published under
https://rennf93.github.io/roboco/ must keep resolving — old links, bookmarks,
and search results don't get to 404 — so instead of real content each gets a
tiny static stub: a meta-refresh + `rel=canonical` pointing at the
docs.roboco.tech equivalent.

The stub set is generated once, while `mkdocs.yml`'s nav still lists every
published page, and the output is committed under `docs-redirects/` (NOT
gitignored, unlike `site/`) because `mkdocs.yml`'s nav is deleted later in
this same phase — nothing durable would be left for a future run to read.
`.github/workflows/docs.yml` deploys that committed directory as-is; it does
not re-run this script.

Section rename: the mirror's "how-to" tour section is called "tour" on
docs.roboco.tech (per the spec's slug map). Every other section slug is
identical between the two sites — verified with --verify-nav-ts against the
website repo's nav.ts before this stub set was committed.

Usage:
    uv run python scripts/gen_docs_redirects.py
    uv run python scripts/gen_docs_redirects.py \\
        --verify-nav-ts ../roboco-website/src/content/docs/nav.ts
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MKDOCS_YML = Path("mkdocs.yml")
OUT_DIR = Path("docs-redirects")
DEST_BASE = "https://docs.roboco.tech/docs"

SECTION_RENAME = {"how-to": "tour"}

STUB_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta http-equiv="refresh" content="0; url={dest}">
<link rel="canonical" href="{dest}">
</head>
<body>
<p>This page has moved to <a href="{dest}">{dest}</a>.</p>
</body>
</html>
"""


@dataclass(frozen=True)
class Page:
    title: str
    md_path: str  # as written in mkdocs.yml nav, e.g. "optional/http-security.md"


class _TolerantLoader(yaml.SafeLoader):
    """mkdocs.yml's `markdown_extensions` block carries `!!python/name:...`
    tags (pymdownx wiring). We only read `nav:`, so tolerate them instead of
    failing to parse the file."""


_TolerantLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/name:", lambda _loader, suffix, _node: suffix
)


def load_nav_pages(mkdocs_yml: Path) -> list[Page]:
    config = yaml.load(mkdocs_yml.read_text(), Loader=_TolerantLoader)
    return list(_walk_nav(config["nav"]))


def _walk_nav(items: list[Any], section_title: str | None = None) -> Any:
    for item in items:
        if isinstance(item, str):
            yield Page(title=section_title or "RoboCo Docs", md_path=item)
        elif isinstance(item, dict):
            for title, value in item.items():
                if isinstance(value, str):
                    yield Page(title=title, md_path=value)
                else:
                    yield from _walk_nav(value, section_title=title)


def _slug_parts(md_path: str) -> tuple[str, ...]:
    """Directory/stem parts of an md path with a trailing index/README
    collapsed onto the enclosing directory — mirrors MkDocs'
    `use_directory_urls` scheme (the default, and what this site used)."""
    path = Path(md_path)
    parts = path.parts[:-1]
    if path.stem not in ("index", "README"):
        parts = (*parts, path.stem)
    return parts


def source_dir(md_path: str) -> Path:
    """Output directory (relative to OUT_DIR) holding this page's stub —
    the exact directory GitHub Pages already serves it from today."""
    return Path(*_slug_parts(md_path))


def dest_url(md_path: str) -> str:
    parts = _slug_parts(md_path)
    if not parts:
        return DEST_BASE
    renamed = (SECTION_RENAME.get(parts[0], parts[0]), *parts[1:])
    return f"{DEST_BASE}/{'/'.join(renamed)}"


def render_stub(title: str, dest: str) -> str:
    return STUB_TEMPLATE.format(title=title, dest=dest)


def generate(pages: list[Page], out_dir: Path) -> list[tuple[str, str]]:
    """Write one stub per page; return [(source_url_path, dest_url), ...]."""
    mapping = []
    for page in pages:
        rel_dir = source_dir(page.md_path)
        dest = dest_url(page.md_path)
        out_path = out_dir / rel_dir / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_stub(page.title, dest))
        source_url_path = "/" if str(rel_dir) == "." else f"/{rel_dir.as_posix()}/"
        mapping.append((source_url_path, dest))
    return mapping


def verify_against_nav_ts(mapping: list[tuple[str, str]], nav_ts: Path) -> list[str]:
    """Every stub's destination must resolve to a real docs.roboco.tech page.
    Returns the unmapped destinations (empty = every stub target exists)."""
    nav_slugs = set(re.findall(r'slug:\s*"([^"]+)"', nav_ts.read_text()))
    unmapped = []
    for _source, dest in mapping:
        slug = dest.removeprefix(f"{DEST_BASE}/").removeprefix(DEST_BASE)
        if slug and slug not in nav_slugs:
            unmapped.append(dest)
    return unmapped


def main() -> int:
    args = sys.argv[1:]
    verify_path = (
        Path(args[args.index("--verify-nav-ts") + 1])
        if "--verify-nav-ts" in args
        else None
    )

    pages = load_nav_pages(MKDOCS_YML)
    mapping = generate(pages, OUT_DIR)

    print(f"Generated {len(mapping)} redirect stub(s) under {OUT_DIR}/:")
    for source, dest in mapping:
        print(f"  {source:55s} -> {dest}")

    if verify_path is not None:
        unmapped = verify_against_nav_ts(mapping, verify_path)
        if unmapped:
            print(f"\n{len(unmapped)} unmapped target(s) (no matching nav.ts entry):")
            for target in unmapped:
                print(f"  {target}")
            return 1
        print(f"\nAll {len(mapping)} stub targets verified against {verify_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
