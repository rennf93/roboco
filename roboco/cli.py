"""
RoboCo CLI

Command-line interface for the RoboCo system.
Separates CLI argument parsing from bootstrap logic.
"""

import argparse
import asyncio

from roboco.bootstrap import main
from roboco.config import resolve_uvicorn_loop_factory, settings
from roboco.db import bootstrap_database


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="RoboCo Bootstrap")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip database initialization",
    )
    parser.add_argument(
        "--skip-orchestrator",
        action="store_true",
        help="Skip starting the orchestrator",
    )
    parser.add_argument(
        "--spawn",
        nargs="*",
        help="Agent IDs to spawn immediately",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="Only initialize database, then exit",
    )
    return parser.parse_args()


def cli() -> None:
    """CLI entry point."""
    args = parse_args()
    # Sets the process-wide event loop for the whole run — including the
    # API server started inside main() via Server.serve(), which never
    # reads uvicorn's own Config.loop (see resolve_uvicorn_loop_factory).
    loop_factory = resolve_uvicorn_loop_factory(settings.uvicorn_loop)

    if args.db_only:
        asyncio.run(bootstrap_database(), loop_factory=loop_factory)
    else:
        asyncio.run(
            main(
                skip_db=args.skip_db,
                skip_orchestrator=args.skip_orchestrator,
                spawn_agents=args.spawn,
            ),
            loop_factory=loop_factory,
        )


if __name__ == "__main__":
    cli()
