"""Images whose runtime user is not root never depend on the build folder's modes.

`COPY` keeps each file's mode from the build context. A GitHub checkout has
0644/0755, but the NAS deploy copy is owner-only (0600), so on 2026-11-01 the
decisions image (uid 10001) could not read /app/server.py and crash-looped,
which stopped deploy-nas.sh before it switched nginx: every request got 502.
CI builds from a checkout and never saw it. So every file a non-root final
stage copies from the build context must carry `--chmod`, a `--chown`, or an
explicit `chmod` of its destination later in the same stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# tests/unit/test_dockerfile_copy_modes.py
#   parents[0] = unit
#   parents[1] = tests
#   parents[2] = <repo root>
_DOCKER_DIR = Path(__file__).parents[2] / "docker"
_ROOT_USERS = frozenset({"root", "0", "0:0", "root:root"})
_LOCAL_IMAGE = re.compile(r"^roboco-(?P<name>[a-z0-9-]+)(?::[\w.-]+)?$")


@dataclass
class _Stage:
    user: str | None
    copies: list[tuple[int, str]] = field(default_factory=list)
    runs: list[tuple[int, str]] = field(default_factory=list)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """``(first line number, instruction)`` with continuations joined and
    comments dropped."""
    lines: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start = number
        if stripped.startswith("#"):
            continue
        pending.append(stripped.removesuffix("\\").strip())
        if not stripped.endswith("\\"):
            lines.append((start, " ".join(pending)))
            pending = []
    return lines


def _stages(text: str, inherited_user: dict[str, str | None]) -> list[_Stage]:
    stages: list[_Stage] = []
    for number, line in _logical_lines(text):
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        if keyword == "FROM":
            image = rest.split()[0]
            local = _LOCAL_IMAGE.match(image)
            user = inherited_user.get(local["name"]) if local else None
            stages.append(_Stage(user))
        elif keyword == "USER" and stages:
            stages[-1].user = rest.strip()
        elif keyword == "COPY" and stages:
            stages[-1].copies.append((number, rest))
        elif keyword == "RUN" and stages:
            stages[-1].runs.append((number, rest))
    return stages


def _runtime_user(path: Path, seen: frozenset[Path] = frozenset()) -> str | None:
    """The final stage's user, following ``FROM roboco-<name>`` to
    ``docker/<name>.Dockerfile``."""
    inherited: dict[str, str | None] = {}
    for match in re.finditer(r"^FROM\s+roboco-([a-z0-9-]+)", path.read_text(), re.M):
        base = _DOCKER_DIR / f"{match[1]}.Dockerfile"
        if base.is_file() and base not in seen:
            inherited[match[1]] = _runtime_user(base, seen | {path})
    stages = _stages(path.read_text(), inherited)
    return stages[-1].user if stages else None


def copies_without_a_mode(text: str, runtime_user: str | None) -> list[int]:
    """Line numbers of build-context COPYs in the final stage that leave the
    file's mode to the build folder, when that stage runs as a non-root user."""
    if runtime_user is None or runtime_user in _ROOT_USERS:
        return []
    final = _stages(text, {})[-1]
    loose = []
    for number, args in final.copies:
        if "--from=" in args or "--chmod=" in args or "--chown=" in args:
            continue
        destination = args.split()[-1]
        later_chmod = any(
            "chmod" in run and destination in run
            for run_number, run in final.runs
            if run_number > number
        )
        if not later_chmod:
            loose.append(number)
    return loose


@pytest.mark.parametrize(
    "dockerfile", sorted(_DOCKER_DIR.glob("*.Dockerfile")), ids=lambda p: p.name
)
def test_a_non_root_image_sets_the_mode_of_every_file_it_copies_in(
    dockerfile: Path,
) -> None:
    loose = copies_without_a_mode(dockerfile.read_text(), _runtime_user(dockerfile))
    assert not loose, (
        f"{dockerfile.name} lines {loose}: COPY from the build context into a "
        "stage that runs as a non-root user without --chmod, --chown or a later "
        "chmod of the destination; it breaks when the build folder is owner-only"
    )


def test_the_guard_flags_a_bare_copy_into_a_non_root_stage() -> None:
    text = "FROM python:3.12\nCOPY app.py /app/app.py\nUSER 10001:10001\n"
    assert copies_without_a_mode(text, "10001:10001") == [2]


@pytest.mark.parametrize(
    "copy",
    [
        "COPY --chmod=0644 app.py /app/app.py",
        "COPY --chown=10001:10001 app.py /app/app.py",
        "COPY --from=builder /src/app /app/app",
    ],
)
def test_the_guard_accepts_an_explicit_mode_or_owner(copy: str) -> None:
    text = f"FROM python:3.12\n{copy}\nUSER 10001:10001\n"
    assert copies_without_a_mode(text, "10001:10001") == []


def test_the_guard_accepts_a_later_chmod_of_the_destination() -> None:
    text = (
        "FROM python:3.12\nCOPY run.sh /app/run.sh\n"
        "RUN chmod 0755 /app/run.sh \\\n    && true\nUSER 1000:1000\n"
    )
    assert copies_without_a_mode(text, "1000:1000") == []


def test_the_guard_ignores_root_images_and_earlier_stages() -> None:
    text = (
        "FROM node:22 AS build\nCOPY src /src\n"
        "FROM node:22\nCOPY --from=build /src /app\n"
    )
    assert copies_without_a_mode(text, None) == []
    assert copies_without_a_mode(text, "root") == []
    assert copies_without_a_mode(text, "1000") == []


def test_a_role_image_inherits_the_base_images_user() -> None:
    assert _runtime_user(_DOCKER_DIR / "agent-base.Dockerfile") == "1000:1000"
