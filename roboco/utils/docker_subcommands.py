"""Every docker CLI subcommand RoboCo runs.

The orchestrator reaches Docker only through the ``docker`` CLI, so this list
is its whole Docker surface. It is exact in both directions:
``tests/unit/utils/test_docker_runs_only_listed_subcommands.py`` reads every
docker argv in ``roboco/`` and fails on a subcommand missing here and on an
entry here that nothing runs any more. Adding a docker call means adding its
subcommand here, in review.

A management command is written with its verb (``container inspect``). An
inspect always names its object type: a bare ``docker inspect`` of a name no
container has goes on to look for an image, network or volume of that name
and reports that object instead.
"""

from __future__ import annotations

DOCKER_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        # Agent containers (every provider runs the one argv the orchestrator
        # builds), the Intake and Secretary chats, sandboxes, and the
        # throwaway containers a clone's own code runs in.
        "run",
        # Liveness, exit state and id of a container, the orchestrator's own
        # image and compose project.
        "container inspect",
        # Whether an agent or sandbox image is present before a pull or build.
        "image inspect",
        "pull",
        # Local agent images when no image registry is configured.
        "build",
        # The dangling-image sweep.
        "image prune",
        # Probes and token reads in agent containers, sandbox setup and probes.
        "exec",
        # Work-signal reads and the log dump kept before a container is removed.
        "logs",
        # Stopping agents, sandboxes and timed-out throwaway containers.
        "stop",
        "kill",
        "rm",
        # The sandbox janitor's listings.
        "ps",
    }
)
