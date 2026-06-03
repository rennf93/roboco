"""Bump the project version.

Updates the `version` field in `pyproject.toml` and scaffolds a matching
release section in `CHANGELOG.md` (Keep a Changelog format). RoboCo ships as a
Docker image / GitHub release rather than a PyPI package, so there is no
`.mike.yml` or `versions.json` to maintain — only these two files.

Usage:
    python .github/scripts/bump_version.py X.Y.Z
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_EXPECTED_ARGC = 2  # script name + version argument


def update_pyproject_toml(version: str) -> bool:
    """Set the `version = "..."` field in pyproject.toml."""
    path = PROJECT_ROOT / "pyproject.toml"
    content = path.read_text()
    pattern = re.compile(r'^(version\s*=\s*)"[^"]*"', re.MULTILINE)
    match = pattern.search(content)
    if not match:
        print("  ERROR: Could not find version field in pyproject.toml")
        return False
    current = re.search(r'"([^"]*)"', match.group(0))
    if current and current.group(1) == version:
        print(f"  pyproject.toml: already set to {version}")
        return True
    new_content = pattern.sub(f'{match.group(1)}"{version}"', content)
    path.write_text(new_content)
    print(f"  pyproject.toml: updated to {version}")
    return True


def update_changelog(version: str) -> bool:
    """Insert a `## [version] - DATE` section after `## [Unreleased]`.

    The Unreleased section is left in place (empty) so future changes have a
    home. If a section for this version already exists, nothing is changed.
    """
    path = PROJECT_ROOT / "CHANGELOG.md"
    content = path.read_text()
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    if re.search(rf"^## \[{re.escape(version)}\]", content, re.MULTILINE):
        print(f"  CHANGELOG.md: [{version}] entry already exists")
        return True

    unreleased = re.search(r"^## \[Unreleased\].*$", content, re.MULTILINE)
    if not unreleased:
        print("  ERROR: Could not find '## [Unreleased]' in CHANGELOG.md")
        return False

    section = f"## [{version}] - {today}\n\n### Added\n\n### Changed\n\n### Fixed\n\n"

    # Find the next section header after Unreleased; insert the new section
    # immediately before it (or at end-of-file if Unreleased is the last one).
    after_unreleased = content[unreleased.end() :]
    next_header = re.search(r"^## \[", after_unreleased, re.MULTILINE)
    if next_header:
        insert_at = unreleased.end() + next_header.start()
        new_content = content[:insert_at] + section + content[insert_at:]
    else:
        new_content = content.rstrip() + "\n\n" + section

    path.write_text(new_content)
    print(f"  CHANGELOG.md: added [{version}] scaffold")
    return True


def main() -> int:
    """Entry point: validate the version argument and run all updaters."""
    if len(sys.argv) != _EXPECTED_ARGC:
        print("Usage: bump_version.py <version>")
        print("  version must be in X.Y.Z format")
        return 1

    version = sys.argv[1]

    if not VERSION_PATTERN.match(version):
        print(f"Error: '{version}' is not a valid version. Expected format: X.Y.Z")
        return 1

    print(f"Bumping version to {version}...\n")

    updaters: list[tuple[str, Callable[[str], bool]]] = [
        ("pyproject.toml", update_pyproject_toml),
        ("CHANGELOG.md", update_changelog),
    ]

    all_ok = True
    for name, updater in updaters:
        try:
            if not updater(version):
                print(f"\n  FAILED: {name}")
                all_ok = False
        except Exception as exc:  # report and continue past one bad updater
            print(f"\n  ERROR updating {name}: {exc}")
            all_ok = False

    print()
    if all_ok:
        print("Version bump complete.")
    else:
        print("Version bump completed with errors.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
