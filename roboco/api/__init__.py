"""
RoboCo API

FastAPI application with WebSocket support for real-time communication.

NOTE: this package's `__init__.py` deliberately does NOT re-export `app` or
`create_app` from `roboco.api.app`. Doing so triggered a transitive load of
the FastAPI app + every route module whenever any code imported from
`roboco.api.schemas.X`, which closed circular-import cycles whenever a
`services/X.py` imported a request schema (services → api.schemas → api/__init__
→ api.app → routes → services). The bootstrap entrypoint imports the app
directly via `roboco.api.app:app`.
"""
