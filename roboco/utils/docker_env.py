"""Keep ``docker run -e NAME=value`` values out of the docker CLI's argv.

Every process in the orchestrator container can read any other process's
command line from ``/proc/<pid>/cmdline``, but only the same uid (or root) can
read its environment. ``docker run -e NAME`` (no ``=``) takes the value from
the docker CLI's own environment, so moving each value there sets the exact
same variable in the container while the agent token, provider API keys and
test-DB password stop showing up in ``ps`` for the duration of ``docker run``.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ENV_FLAGS = frozenset({"-e", "--env"})
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def docker_options_without_env_values(
    options: Sequence[str],
) -> tuple[list[str], dict[str, str]]:
    """Split ``docker run`` options into value-free argv and the env values.

    ``options`` must hold only ``docker``/``run`` and their options, never the
    image or the container's own command: past the image, ``-e`` belongs to
    the container command and must not be rewritten. Every ``-e NAME=value``,
    ``--env NAME=value`` and ``--env=NAME=value`` becomes ``-e NAME`` and the
    value lands in the returned mapping (last occurrence wins, as in docker).
    A bare ``-e NAME`` already passes its value through the CLI environment
    and is left as is.
    """
    argv: list[str] = []
    values: dict[str, str] = {}
    pending_flag = False
    for token in options:
        if pending_flag:
            pending_flag = False
            argv.append(_record_env_value(token, values))
            continue
        if token in _ENV_FLAGS:
            argv.append("-e")
            pending_flag = True
            continue
        if token.startswith("--env="):
            argv.extend(["-e", _record_env_value(token[len("--env=") :], values)])
            continue
        argv.append(token)
    if pending_flag:
        raise ValueError("docker options end with a dangling -e flag")
    return argv, values


def _record_env_value(assignment: str, values: dict[str, str]) -> str:
    """Store ``NAME=value`` in ``values`` and return the bare ``NAME``."""
    name, sep, value = assignment.partition("=")
    if not _ENV_NAME.fullmatch(name):
        raise ValueError(f"not a docker env assignment: {name!r}")
    if sep:
        values[name] = value
    return name


def docker_cli_env(values: Mapping[str, str]) -> dict[str, str]:
    """The docker CLI's environment: the orchestrator's own plus ``values``."""
    return {**os.environ, **values}
