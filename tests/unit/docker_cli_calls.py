"""Read a mocked ``docker run`` call the way the container sees it.

Spawn paths pass ``-e NAME`` in argv and the value through the docker CLI's
own environment (``roboco.utils.docker_env``), so the value never appears in
``/proc/<pid>/cmdline``. Tests that assert on what the container gets rebuild
the ``-e NAME=value`` form here; tests that assert the value stays out of argv
read ``call.args`` directly.
"""

from __future__ import annotations

from typing import Any


def docker_run_cmd_with_env_values(call: Any) -> list[str]:
    """``call.args`` with each bare ``-e NAME`` expanded from ``call.kwargs['env']``."""
    argv = list(call.args)
    env = call.kwargs.get("env") or {}
    expanded: list[str] = []
    expand_next = False
    for token in argv:
        if expand_next and "=" not in token and token in env:
            expanded.append(f"{token}={env[token]}")
        else:
            expanded.append(token)
        expand_next = token == "-e"
    return expanded


def docker_argv_env_assignments(call: Any) -> list[str]:
    """Every ``-e`` operand in the raw argv that still carries a ``=value``."""
    argv = list(call.args)
    return [
        argv[i + 1]
        for i, token in enumerate(argv[:-1])
        if token == "-e" and "=" in argv[i + 1]
    ]
