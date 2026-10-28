"""Write a refreshed provider credential readable by the agent user alone.

The orchestrator (root) rewrites ``auth.json`` in place when it refreshes a
grok / codex token. Agent containers run as uid 1000 and bind-mount that file
read-only, so the agent uid must own it; nobody else in the orchestrator, the
workspace git user included, may read it. Every rewrite therefore lands as
``<agent uid>:<unchanged gid>`` with mode 0600, whatever the old file had.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Same knob workspace ownership has always used for the agent image's uid.
AGENT_UID = int(os.environ.get("ROBOCO_AGENT_UID", "1000"))

_OWNER_ONLY = 0o600
# No O_NOFOLLOW: the path is operator-configured (ROBOCO_HOST_*_DIR), never
# agent-writable, and an operator-symlinked auth.json must still receive the
# rotated single-use refresh token (F006).
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC


def write_agent_credential(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` as an agent-owned, owner-only file.

    Ownership and mode are fixed on the open descriptor BEFORE any byte is
    written, so the secret is never readable at a looser mode, not even for
    the instant between create and chmod. Only root can hand a file to
    another uid; an unprivileged caller (dev tooling, tests) already owns
    what it writes.
    """
    fd = os.open(path, _WRITE_FLAGS, _OWNER_ONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        if os.geteuid() == 0:
            os.fchown(fd, AGENT_UID, -1)
        os.fchmod(fd, _OWNER_ONLY)
        handle.write(payload)
